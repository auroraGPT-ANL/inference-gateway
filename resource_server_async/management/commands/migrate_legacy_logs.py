from __future__ import annotations

import json
import uuid
from typing import Iterable, List, Optional, Tuple
import hashlib

from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction
from django.utils.timezone import now

from resource_server.models import Log as LegacyLog
from resource_server.models import Endpoint as LegacyEndpoint
from resource_server_async.models import (
    User,
    AccessLog,
    RequestLog,
    AuthService,
)


def _det_uuid(namespace_prefix: str, legacy_id: int) -> uuid.UUID:
    return uuid.uuid5(
        uuid.NAMESPACE_URL, f"inference-gateway:{namespace_prefix}:{legacy_id}"
    )


def _parse_endpoint_slug(endpoint_slug: Optional[str]) -> Tuple[str, str, str]:
    if not endpoint_slug:
        return ("unknown", "unknown", "unknown")

    parts = endpoint_slug.strip().lower().split("-")
    if len(parts) < 3:
        return (
            parts[0] if parts else "unknown",
            parts[1] if len(parts) > 1 else "unknown",
            "unknown",
        )

    cluster = parts[0]
    framework = parts[1]
    model = "-".join(parts[2:])
    return (cluster, framework, model)


def _truncate(value: Optional[str], max_len: int) -> Optional[str]:
    if value is None:
        return None
    if len(value) <= max_len:
        return value
    return value[:max_len]


def _derive_timestamps(
    ts_receive,
    ts_submit,
    ts_response,
) -> Tuple:
    # compute_request
    compute_req = ts_submit or ts_receive or ts_response or now()

    # compute_response
    compute_res = ts_response or ts_submit or ts_receive or now()

    # Ensure ordering (avoid response < request)
    if compute_res < compute_req:
        compute_res = compute_req

    return compute_req, compute_res


def _safe_status_code(
    response_status: Optional[int], result: Optional[str]
) -> Tuple[int, Optional[str]]:
    if response_status is None:
        # Unknown status; keep result as-is but mark status 0
        return 0, None
    if int(response_status) == 200:
        return 200, None
    # Non-200 → capture error payload if available (truncate if needed)
    error = None
    if result:
        try:
            # If it's JSON, keep compact string; else store raw
            error_json = json.loads(result)
            error = json.dumps(error_json, separators=(",", ":"))
        except Exception:
            error = result
    return int(response_status), error


class Command(BaseCommand):
    help = "Migrate legacy resource_server.Log rows into resource_server_async tables."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--ids",
            nargs="+",
            type=int,
            help="Specific legacy Log IDs to migrate (space-separated)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Do not write to DB; print what would change",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Process rows in batches for large migrations",
        )

    def handle(self, *args, **options):
        ids: Optional[List[int]] = options.get("ids")
        dry_run: bool = options.get("dry_run", False)
        batch_size: int = options.get("batch_size")

        if not ids:
            self.stdout.write(self.style.WARNING("No --ids provided. Nothing to do."))
            return

        queryset = LegacyLog.objects.filter(id__in=ids).order_by("id")
        total = queryset.count()
        if total == 0:
            self.stdout.write(self.style.WARNING("No legacy rows found for given IDs."))
            return

        migrated = 0
        skipped = 0

        # For small ID lists, one transaction is OK. For larger, use per-batch commits.
        iterator: Iterable[LegacyLog] = queryset.iterator(
            chunk_size=min(batch_size, max(total, 1))
        )

        @transaction.atomic
        def _migrate_one(row: LegacyLog):
            nonlocal migrated, skipped

            # Build or recover User (idempotent on synthetic id)
            username = (row.username or "unknown").strip().lower()
            name = row.name or username or "Unknown"
            # Ensure user_id fits CharField(100)
            candidate_id = f"legacy:{username}" if username else f"legacy:{row.id}"
            if len(candidate_id) > 100:
                h = hashlib.sha1(username.encode("utf-8")).hexdigest()
                candidate_id = f"legacy:{h}"
            user_id = candidate_id

            user_defaults = {
                "name": name,
                "username": username,
                "idp_id": "",
                "idp_name": "Legacy",
                "auth_service": AuthService.GLOBUS.value,
            }

            if dry_run:
                user_obj = None  # Not created
            else:
                user_obj, _ = User.objects.get_or_create(
                    id=user_id, defaults=user_defaults
                )

            # AccessLog mapping
            access_id = _det_uuid("access", row.id)

            status_code, error_payload = _safe_status_code(
                row.response_status, row.result
            )
            api_route = f"/legacy/{(row.openai_endpoint or 'unknown').strip('/')}"
            origin_ip = "legacy"

            access_defaults = {
                "user": user_obj if not dry_run else None,
                "timestamp_request": row.timestamp_receive,
                "timestamp_response": row.timestamp_response,
                "api_route": api_route,
                "origin_ip": origin_ip,
                "status_code": status_code,
                "error": error_payload,
                "authorized_groups": None,
            }

            # Derive cluster/framework/model
            cluster = framework = model = None
            if row.endpoint_slug:
                try:
                    ep = (
                        LegacyEndpoint.objects.filter(endpoint_slug=row.endpoint_slug)
                        .only("cluster", "framework", "model")
                        .first()
                    )
                    if ep:
                        cluster, framework, model = ep.cluster, ep.framework, ep.model
                except Exception:
                    pass
            if not all([cluster, framework, model]):
                cluster, framework, model = _parse_endpoint_slug(row.endpoint_slug)

            # Enforce max lengths on CharFields
            cluster = _truncate(cluster, 100) or "unknown"
            framework = _truncate(framework, 100) or "unknown"
            model = _truncate(model, 100) or "unknown"

            compute_req, compute_res = _derive_timestamps(
                row.timestamp_receive, row.timestamp_submit, row.timestamp_response
            )

            request_id = _det_uuid("request", row.id)
            request_defaults = {
                "cluster": cluster,
                "framework": framework,
                "model": model,
                "openai_endpoint": _truncate(
                    (row.openai_endpoint or "chat/completions").strip("/"), 100
                ),
                "timestamp_compute_request": compute_req,
                "timestamp_compute_response": compute_res,
                "prompt": row.prompt,
                "result": row.result,
                "task_uuid": row.task_uuid,
                "metrics_processed": False,
            }

            if dry_run:
                # Print a compact representation of what would be written
                self.stdout.write(
                    json.dumps(
                        {
                            "legacy_id": row.id,
                            "user": {"id": user_id, **user_defaults},
                            "access_log": {
                                "id": str(access_id),
                                **{
                                    k: v
                                    for k, v in access_defaults.items()
                                    if k != "user"
                                },
                            },
                            "request_log": {"id": str(request_id), **request_defaults},
                        },
                        default=str,
                    )
                )
                migrated += 1
                return

            # Upserts (idempotent)
            access_obj, _ = AccessLog.objects.update_or_create(
                id=access_id,
                defaults=access_defaults,
            )

            # Enforce FK link via access_log
            request_values = {**request_defaults, "access_log": access_obj}
            _req, _ = RequestLog.objects.update_or_create(
                id=request_id,
                defaults=request_values,
            )

            migrated += 1

        # Iterate and migrate
        for row in iterator:
            try:
                _migrate_one(row)
            except Exception as e:
                skipped += 1
                self.stderr.write(
                    self.style.ERROR(f"Failed to migrate legacy row {row.id}: {e}")
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Migration complete. migrated={migrated}, skipped={skipped}, total_requested={total}, dry_run={dry_run}"
            )
        )
