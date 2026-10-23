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

Render uses Cloudflare and load balancers. The hop count is configured on the
service rather than hardcoded; recheck it whenever the ingress topology changes.
An example header chain is `forged-entry, visitor, trusted-proxy`; if two entries
are known to be supplied by trusted infrastructure, a count of `2` selects
`visitor`. This example is not a claim about the actual Render deployment.

## Verified production configuration (23 October 2026)

The existing DbCAS service in My Workspace is configured through Render's
service environment variables (no Blueprint or environment group):

```text
VISITOR_LOG_ENABLED=true
VISITOR_LOG_PROXY_HOPS=2
```

Application commit `8c96d8e37b8a651300226cc4acb1341d26e28c1d` is live in deploy
`dep-dbdplsgm7kps73b96gq0`. Checks from the user's current computer confirmed:

- Both `https://dbcas.net` and `https://dbcas.onrender.com` return HTTP 200.
- Normal requests contain two forwarded entries. The entry second from the
  right matches the computer's independently checked public IPv4 address.
- A deliberately forged leftmost entry produces three entries; the resolved
  client IP remains the actual public IPv4 on both hostnames.
- A real homepage reload in the user's Edge browser at **18:37:25 BST**
  (`17:37:25 UTC`) was recorded with `ip_status=resolved`, `ip_version=4`, and
  the same public IP. Its Render request ID is `cdbb29ff-41f6-45e6`.
- No application error logs were found during the deployment/verification window.

The exact public IP is kept in private Render logs and is not copied here.
Both public hostnames currently have no IPv6 AAAA response, so IPv6-only HTTP
requests could not reach them. IPv6 parsing is covered by local tests, but an
end-to-end IPv6 request to these hostnames has **not** been verified. This does
not prevent the user's normal browser from connecting and being logged via IPv4.

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
