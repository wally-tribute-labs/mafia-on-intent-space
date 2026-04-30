"""Host agent package.

The host is a service-intent provider per docs/DECISIONS.md §D1: it
announces phases and resolves protocol-derivable outcomes (tally, kill
resolution) but does not hold canonical game state outside the log.
"""
