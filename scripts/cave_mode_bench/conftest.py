# The task workspaces hold deliberately failing tests (tasks/bugfix) for the agent to fix; they are
# inputs to the benchmark, not part of the test suite.
collect_ignore_glob = ["tasks/*"]
