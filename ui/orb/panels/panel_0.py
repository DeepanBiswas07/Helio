from .base import ReservedPanel


class Panel0(ReservedPanel):
    def __init__(self, *_args, **_kwargs):
        super().__init__(slot_number=1)
