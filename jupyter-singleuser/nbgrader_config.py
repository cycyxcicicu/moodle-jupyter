c = get_config()  # noqa: F821

# Cấu hình Exchange dùng chung cho mọi user
c.Exchange.root = "/srv/nbgrader/exchange"
c.Exchange.path_includes_course = True
