"""ReCRATE: reuse published methods on your data, checked and recorded.

A paper's method is described by an input RO-Crate (supplied by the authors or
drafted from the paper). ReCRATE uses it to:

1. reproduce the paper's reported result on the paper's own data,
2. check whether *your* data fits the method (via a domain profile),
3. run the method on your data, recording any adjustment,
4. write every run as a Workflow Run RO-Crate linked back to the paper.
"""

__version__ = "0.1.0"

from .crate import MethodSpec, load_crate  # noqa: E402,F401
from .pipeline import check_inputs, reproduce, run_on_data  # noqa: E402,F401
