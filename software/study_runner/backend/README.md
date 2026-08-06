# Backend — the server

Two folders, and the split between them is the point:

- **`routes/`** decides what a URL means. One file per area of the HTTP surface.
  A route parses the request, calls one service, and turns the answer into JSON.
  It should be short enough to read in one go.
- **`services/`** does the work, and knows nothing about Flask requests. See
  [`services/README.md`](services/README.md) for how those five folders are
  divided.

`__init__.py` is the app factory: it resolves runtime paths, loads settings,
constructs the long-lived services, registers the routes, and installs the cache
policy that stops a tablet serving yesterday's interface.

## The HTTP surface is declared, not discovered

`software/tests/test_route_inventory.py` holds the full list of routes this app
answers. Adding a route means adding it there too, and the test fails if the app
grows or loses one silently. That list is the closest thing to an API document,
and it is checked on every run rather than written once.

## Where the boundaries actually are

- A route may not do file I/O, talk to a plugin, or hold state. If it needs to,
  that belongs in a service.
- A service may not import Flask, read `current_app`, or raise HTTP errors. It
  raises its own exception type and the route decides on a status code.
- Neither may name a plugin. Plugin behaviour comes from
  `plugin_framework/registry.py`, driven by manifests.
- Anything long-lived — the sensor coordinator, the session store, the recording
  runtime — is built once in the app factory and read from `app.config`, not
  constructed per request.
