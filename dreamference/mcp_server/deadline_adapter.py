"""requests' transport adapter with connections that obey the call's RequestDeadline."""

import requests.adapters
import urllib3

from dreamference.mcp_server.request_deadline import deadline_pools


class DeadlineAdapter(requests.adapters.HTTPAdapter):
    """requests' adapter, direct or through a proxy, whose sockets register with the deadline.

    A proxy has its own pool manager, so a set `http_proxy` would bypass a hook on the direct one.
    """

    def init_poolmanager(
        self, connections: int, maxsize: int, block: bool = False, **pool_kwargs: object
    ) -> None:
        """Builds the direct pool manager, with pool classes from `deadline_pools`."""
        super().init_poolmanager(connections, maxsize, block, **pool_kwargs)
        self.poolmanager.pool_classes_by_scheme = deadline_pools(
            self.poolmanager.pool_classes_by_scheme
        )

    def proxy_manager_for(self, proxy: str, **proxy_kwargs: object) -> urllib3.PoolManager:
        """Returns the pool manager for `proxy`, with pool classes from `deadline_pools`."""
        manager = super().proxy_manager_for(proxy, **proxy_kwargs)
        manager.pool_classes_by_scheme = deadline_pools(manager.pool_classes_by_scheme)
        return manager
