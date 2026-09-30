"""LocalAgreement-2 (Macháček et al., 2023, *whisper_streaming*) at character level.

The prefix shared by the last two decodings of the open segment is committed
("stable", shown in white) and never retracted; the rest is "unstable" (grey).
"""

from __future__ import annotations

import os


class LocalAgreement:
    def __init__(self) -> None:
        self._prev = ""
        self.committed = ""

    def update(self, hypothesis: str) -> tuple[str, str]:
        agreed = os.path.commonprefix([self._prev, hypothesis])
        if len(agreed) > len(self.committed) and agreed.startswith(self.committed):
            self.committed = agreed
        self._prev = hypothesis
        shared = len(os.path.commonprefix([self.committed, hypothesis]))
        return self.committed, hypothesis[shared:]
