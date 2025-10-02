"""
Globus OAuth2 authentication utilities for dashboard.
"""

import globus_sdk
from django.conf import settings
from django.contrib.auth import get_user_model
from utils.auth_utils import introspect_token, check_globus_policies, check_globus_groups, check_session_info, check_groups_per_idp
import logging

log = logging.getLogger(__name__)

User = get_user_model()


def get_globus_oauth_client():
    """Get configured Globus OAuth2 client for dashboard."""
    return globus_sdk.ConfidentialAppAuthClient(
        settings.GLOBUS_DASHBOARD_APPLICATION_ID,
        settings.GLOBUS_DASHBOARD_APPLICATION_SECRET
    )


def get_authorization_url(state=None):
    """
    Generate Globus authorization URL for OAuth2 flow.
    
    Args:
        state: Optional state parameter for CSRF protection
        
    Returns:
        str: authorization_url
    """
    client = get_globus_oauth_client()
    
    # Generate auth URL
    client.oauth2_start_flow(
        settings.GLOBUS_DASHBOARD_REDIRECT_URI,
        requested_scopes=settings.GLOBUS_DASHBOARD_SCOPES,
        refresh_tokens=True,
        state=state  # Pass state to the flow initialization
    )
    
    # Get the authorize URL
    auth_url = client.oauth2_get_authorize_url()
    
    # Add optional policy parameters to the URL if configured
    if settings.NUMBER_OF_GLOBUS_POLICIES > 0:
        # Append policy parameters to the URL
        separator = '&' if '?' in auth_url else '?'
        auth_url = f"{auth_url}{separator}session_required_policies={settings.GLOBUS_POLICIES}"
    
    return auth_url


def exchange_code_for_tokens(auth_code):
    """
    Exchange authorization code for access tokens.
    
    Args:
        auth_code: Authorization code from callback
        
    Returns:
        dict: Token response with access_token, refresh_token, expires_in, etc.
    """
    client = get_globus_oauth_client()
    
    client.oauth2_start_flow(
        settings.GLOBUS_DASHBOARD_REDIRECT_URI,
        requested_scopes=settings.GLOBUS_DASHBOARD_SCOPES,
        refresh_tokens=True
    )
    
    token_response = client.oauth2_exchange_code_for_tokens(auth_code)
    
    # Return all tokens as dict (includes resource_server keys)
    return token_response.by_resource_server


def validate_dashboard_token(access_token, groups_token=None):
    """
    Validate dashboard access token and check group membership.
    
    Args:
        access_token: Globus access token
        groups_token: Optional Groups API token for group membership checks
        
    Returns:
        tuple: (is_valid, user_data, error_message)
    """
    try:
        # Use dashboard client for introspection
        client = get_globus_oauth_client()
        token_info = client.oauth2_token_introspect(access_token)
        
        if not token_info.get('active', False):
            return False, None, "Error: Token is not active or has expired"
        
        # Extract user info from token
        username = token_info.get('username')
        sub = token_info.get('sub')
        
        if not username or not sub:
            return False, None, "Error: Token missing user information"
        
        # Create user object
        class UserInfo:
            def __init__(self, username, sub, token_info):
                self.username = username
                self.id = sub
                self.name = token_info.get('name', username)
                self.email = token_info.get('email', '')
                self.idp_id = token_info.get('identity_provider', '')
                self.idp_name = token_info.get('identity_provider_display_name', '')
        
        user = UserInfo(username, sub, token_info)
        
        # Check dashboard group membership if configured
        if settings.DASHBOARD_GROUP_ENABLED and groups_token:
            log.info(f"Checking group membership for user {username}")
            is_member = check_group_membership(groups_token, sub, settings.GLOBUS_DASHBOARD_GROUP)
            
            if not is_member:
                log.warning(f"Dashboard access denied for {username}: not member of required group")
                return False, None, (
                    f"Dashboard access denied. User '{user.name}' ({user.username}) "
                    f"is not a member of the required Globus Group. "
                    f"Please contact the ALCF operations team to request access to the "
                    f"'ALCF AI Inference Service Dashboard Users' group."
                )
            
            log.info(f"Group membership check passed for {username}")
        
        return True, user, None
        
    except Exception as e:
        log.error(f"Token validation error: {e}")
        return False, None, f"Validation error: {str(e)}"


def check_group_membership(groups_token, user_id, group_id):
    """
    Check if user is a member of the specified Globus Group (with caching).
    
    Args:
        groups_token: Groups API access token
        user_id: User's Globus ID (sub claim)
        group_id: Globus Group ID to check
        
    Returns:
        bool: True if user is a member, False otherwise
    """
    from django.core.cache import cache
    
    # Cache key based on user ID and group ID
    cache_key = f"globus_group_membership:{user_id}:{group_id}"
    
    # Check cache first
    cached_result = cache.get(cache_key)
    if cached_result is not None:
        log.debug(f"Using cached group membership for user {user_id}: {cached_result}")
        return cached_result
    
    # Cache miss - check with Globus API
    try:
        from globus_sdk import GroupsClient, AccessTokenAuthorizer
        
        authorizer = AccessTokenAuthorizer(groups_token)
        groups_client = GroupsClient(authorizer=authorizer)
        
        # Get user's group memberships
        is_member = False
        for group in groups_client.get_my_groups():
            if group['id'] == group_id:
                is_member = True
                break
        
        if is_member:
            log.info(f"User is member of group {group_id}")
        else:
            log.warning(f"User is not a member of group {group_id}")
        
        # Cache result for 10 minutes (600 seconds)
        # This balances security (timely revocation) with performance
        cache.set(cache_key, is_member, timeout=600)
        
        return is_member
        
    except Exception as e:
        log.error(f"Group membership check error: {e}")
        # On error, don't cache and deny access for security
        return False


def refresh_access_token(refresh_token):
    """
    Refresh an expired access token.
    
    Args:
        refresh_token: Globus refresh token
        
    Returns:
        dict: New token response
    """
    client = get_globus_oauth_client()
    
    authorizer = globus_sdk.RefreshTokenAuthorizer(
        refresh_token,
        client,
        access_token=None,
        expires_at=None
    )
    
    # Force token refresh
    new_access_token = authorizer.get_authorization_header()
    
    # Get new token info
    token_response = authorizer.check_expiration_time()
    
    return {
        'access_token': new_access_token.split(' ')[1],  # Remove 'Bearer ' prefix
        'expires_at': authorizer.expires_at,
    }


def revoke_token(token):
    """
    Revoke a Globus token (logout).
    
    Args:
        token: Access or refresh token to revoke
    """
    try:
        client = get_globus_oauth_client()
        client.oauth2_revoke_token(token)
    except Exception as e:
        log.warning(f"Token revocation error: {e}")

