# OpenMinion reference applications

Complete, independent examples that show where an application ends and the
OpenMinion runtime begins.

| Application | Demonstrates |
| --- | --- |
| [`research-brief`](research-brief/) | read-only research, cited structured output, artifact readback, budgets, and later-turn continuity after restart |
| [`order-care`](order-care/) | app-owned orders, streamed approval, allow-once/deny, effect readback, and uncertain-restart handling |
| [`software-feature-builder`](software-feature-builder/) | bounded feature work, criteria, verifier failure/revision, process restart, and Git truth |

Each folder is independently installable and imports no sibling application.
Repeated code is kept visible until more than one external application proves a
stable reusable contract. The application flows support
`openminion>=0.1.24,<0.2`; CI tests release 0.1.24 and an exact `dev` revision
separately. The local plugin author contract is unreleased, so the order-care
plugin is tested only against the pinned development revision until a release
contains that contract. The release lane records this expected drift rather
than reporting false plugin compatibility.

The examples are maintained teaching references, not hosted services or
production support commitments. Provider-backed smokes are opt-in and require
an explicit OpenMinion config.
