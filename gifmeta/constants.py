"""
Constants and enums relating to GIF files. These are part of the public API.

There aren't actually many enumerations in the GIF format, mainly boolean flags and small integers.
"""

__all__ = (
    "GifVersion",
    "DisposalMethod"
)


from enum import Enum


class GifVersion(Enum):
    """
    Gif version. Note that this is not how it's represented in the file, where it's represented as three ascii characters.
    However, since there are only two valid GIF versions, we can just use an Enum.
    """
    GIF87a = 0
    GIF89a = 1

    def __str__(self) -> str:
        if self is GifVersion.GIF87a:
            return "GIF87a"
        elif self is GifVersion.GIF89a:
            return "GIF89a"
        else:
            raise TypeError("GifVersion must be either 87a or 89a.")


class DisposalMethod(Enum):
    """
    Disposal method for animation frames. Tells how to treat the previous frame after it's been displayed.

    See section 23.c.iv, under Graphic Control Extension.
    """
    NONE = 0
    NO_DISPOSE = 1
    RESTORE_BACKGROUND = 2
    RESTORE_PREVIOUS = 3
