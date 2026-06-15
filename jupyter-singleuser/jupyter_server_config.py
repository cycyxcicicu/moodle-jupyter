import os

c = get_config()  # noqa: F821

MOODLE_WWWROOT = os.environ.get("MOODLE_WWWROOT", "http://localhost:18080")
JUPYTERHUB_COOKIE_SAMESITE = os.environ.get("JUPYTERHUB_COOKIE_SAMESITE", "Lax")
JUPYTERHUB_COOKIE_SECURE = os.environ.get("JUPYTERHUB_COOKIE_SECURE", "False").lower() in ("true", "1", "yes")

c.ServerApp.allow_origin = MOODLE_WWWROOT
c.ServerApp.tornado_settings = {
    'headers': {
        'Content-Security-Policy': f"frame-ancestors 'self' {MOODLE_WWWROOT}",
        'Access-Control-Allow-Origin': MOODLE_WWWROOT,
        'X-Frame-Options': ''
    },
    'cookie_options': {
        'samesite': JUPYTERHUB_COOKIE_SAMESITE,
        'secure': JUPYTERHUB_COOKIE_SECURE
    }
}
