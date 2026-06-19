# Knowledge is partitioned by Collection, not a single global pool

Scout is a shared tool server that multiple agents wire to. Although the README says "search across everything ingested," a single global corpus would leak every agent's (and topic's) Sources into every Search. We partition knowledge by **Collection**: every Source belongs to exactly one Collection, and Searches, Questions, and Sessions are all scoped to a Collection. We rejected the global-pool model because it provides no isolation between callers.

## Consequences

- A Collection identifier threads through the Source data model, the embedding store's search scope, and the Session model — this is deliberately hard to reverse.
- Re-ingesting the same URL or file refreshes the existing Source *within its Collection*; uniqueness is per-Collection, not global.
