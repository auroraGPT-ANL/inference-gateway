import time
from typing import NamedTuple

from redis import Redis


class TokenLimiterCheck(NamedTuple):
    allow: bool
    usage_model: int
    usage_user: int


class TokenRateLimiter:
    def __init__(
        self, redis_client: Redis, prefix: str, *, tpm_model: int, tpm_user: int
    ) -> None:
        """
        Create a rate limiter with a unique `prefix` for the model endpoint with
        desired Tokens per Minute for the model (`tpm_model`) and per-user
        (`tpm_user`).

        Setting tpm limits to 0 disables rate limiting.
        """
        self.redis = redis_client
        self.prefix = f"tpm:{prefix}"
        self.tpm_model = tpm_model
        self.tpm_user = tpm_user

    def _keys(self, user_id: str | None) -> tuple[str, str]:
        minute = int(time.monotonic() // 60)
        model_key = f"{self.prefix}:{minute}"
        user_key = f"{self.prefix}:{user_id}:{minute}"
        return model_key, user_key

    def check(self, user_id: str) -> TokenLimiterCheck:
        """
        Check if rate limiter should allow call and return current usage.
        """
        model_key, user_key = self._keys(user_id)
        try:
            current_model = int(self.redis.get(model_key) or 0)  # type: ignore
            current_user = int(self.redis.get(user_key) or 0)  # type: ignore
        except ValueError:
            current_model, current_user = 0, 0

        allow = True
        if self.tpm_model > 0:
            allow = allow and (current_model < self.tpm_model)
        if self.tpm_user > 0:
            allow = allow and (current_user < self.tpm_user)

        return TokenLimiterCheck(allow, current_model, current_user)

    def record(self, user_id: str | None, tokens: int) -> None:
        """
        Call after LLM response with actual token counts to record the usage of the model.
        """
        model_key, user_key = self._keys(user_id)
        pipe = self.redis.pipeline(transaction=True)

        pipe.incrby(model_key, tokens)
        pipe.expire(model_key, 120)  # 2x window so stragglers don't lose the key

        # Guard in case of anonymous usage:
        if user_id:
            pipe.incrby(user_key, tokens)
            pipe.expire(user_key, 120)  # 2x window so stragglers don't lose the key

        pipe.execute()
