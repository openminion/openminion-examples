# Versioning

The current supported range is `openminion>=0.1.24,<0.2`. Hosted CI records the
exact released version, exact development commit, and turn-stream schema for
each application. A range change requires all provider-free suites to pass.
Application databases remain readable when the dependency is rolled back to
the prior supported release.
