"""One lock for all Matplotlib rendering, shared by interactive charts and PDF exports."""
from functools import wraps
from threading import RLock

# Matplotlib font/rendering state is shared even by independent figures.
# PDF rendering takes this lock per page, never for an entire export.
RENDER_LOCK = RLock()


def serialized_render(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with RENDER_LOCK:
            return function(*args, **kwargs)
    return wrapped
