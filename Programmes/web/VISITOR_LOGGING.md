# Visitor IP diagnostics

DbCAS writes one JSON event (`dbcas.visit`) for each non-static request received
by Flask. This works on all application pages, even when JavaScript or the
MapMyVisitors widget does not run. The event records UTC time, the route pattern,
HTTP method/status, request ID, network peer and reported proxy IP headers.
IPv4 and IPv6 are supported. Search strings, cookies, authorization headers and
arbitrary unmatched URL paths are not recorded by this logger.

These are private operational logs, not a public visitor list. No IP address is
returned to the browser or added to the public map. Render captures the JSON
output in the service's **Logs** page; search for `dbcas.visit` or the request's
`Rndr-Id`. Keep dashboard access restricted to site administrators.

## Deployment settings

- `VISITOR_LOG_ENABLED=true` enables logging. It defaults to enabled when Render
  sets `RENDER=true`, and disabled outside Render. Set it to `false` to stop it.
- `VISITOR_LOG_PROXY_HOPS` is the number of trusted `X-Forwarded-For` entries to
  count from the **right**, as with Werkzeug's `ProxyFix(x_for=...)`. Set `0` only
  for a direct connection with no reverse proxy. Use a positive count only after
  verifying the deployed proxy chain and ensuring requests cannot bypass it.
- When the hop count is unset, `client_ip` remains null with
  `ip_status=proxy_not_configured`. `peer_ip`, `reported_forwarded_for`,
  `reported_cf_ip` and `reported_cf_ipv6` retain normalized evidence for setup.
  Reported headers are not independently authenticated. The logger deliberately
  does not guess a visitor IP from the leftmost header or an internal proxy.

Render uses Cloudflare and load balancers. The repository does not contain the
service's live proxy configuration, so no production hop count is assumed here.
An example header chain is `forged-entry, visitor, trusted-proxy`; if two entries
are known to be supplied by trusted infrastructure, a count of `2` selects
`visitor`. This example is not a claim about the actual Render deployment.

## Check the deployed configuration

1. Deploy the logging code with the hop count unset. Make a controlled page
   request from a known network, and find its `Rndr-Id` response header in the
   browser Network panel. Find that same ID in Render's application logs.
2. Compare the reported chain against the known public egress IP. Verify the
   proxy behavior for every public hostname and for IPv4/IPv6. Do not simply use
   the length of one header: a caller can prepend fake entries.
3. Set the verified `VISITOR_LOG_PROXY_HOPS`, then repeat with a deliberately
   forged leftmost `X-Forwarded-For` entry. The resolved IP must remain the actual
   requesting network. A short/invalid chain must produce a null client IP, not
   the load balancer's IP.
4. Confirm IP values appear only in the administrator logs. The site's visitor
   diagnostics notice is visible whenever this logging is enabled.

`page_response=true` means a successful HTML GET response, not a unique person.
Bots, health probes and repeat visits can generate these requests. Requests
served entirely by a CDN or browser cache do not reach Flask. A VPN or shared
network exposes its exit IP, not an individual device or a physical address.

## Retention and geolocation

Use Render's existing private log storage rather than writing an unbounded IP
database into the app's temporary filesystem. As checked on 23 October 2026,
Render retains logs for 7 days on Hobby, 14 on Pro, and 30 on Scale/Enterprise.
This change configures no external log stream or longer-term store.

IP capture and IP geolocation are separate. This logger does not change
MapMyVisitors' data, recover old unknown locations, or make a city lookup more
accurate. A future geolocation step should support IPv4/IPv6, distinguish
country-only/city/unknown results, and keep lookup failures separate from missing
IP data. Only aggregated approximate locations belong on the public map.

References:

- [Render: client IP behind its edge](https://render.com/articles/how-render-handles-ddos-attacks)
- [Render: application logs, request IDs and retention](https://render.com/docs/logging)
- [Flask: trusting forwarded headers](https://flask.palletsprojects.com/en/stable/deploying/proxy_fix/)
- [Cloudflare: original visitor IP headers](https://developers.cloudflare.com/fundamentals/reference/http-headers/)
