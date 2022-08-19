from enum import Enum
from mmaputils import MmapCursor
import os
import typing as t

from .constants import *

__all__ = (
    "Colortable",
    "Gif",
    "GifImage",
    "LogicalScreenDescriptor",
    "ImageDescriptor",
    "GraphicControlExtension"
)

# A type alias for color tables.
Colortable = t.Sequence[t.Tuple[int, int, int]]

# Internal constants for reading GIF files.

# Introduces an extension block. Always comes first. The byte after this is the extension label.
EXT_INTRODUCER = 0x21

# Extension labels. See constants.BlockType.
EXT_GRAPHIC_CONTROL_LABEL = 0xF9
EXT_COMMENT_LABEL = 0xFE
EXT_PLAINTEXT_LABEL = 0x01
EXT_APPLICATION_LABEL = 0xFF

# Terminates a GIF file.
TRAILER_LABEL = 0x3B

# Introduces a new image.
IMAGE_SEPARATOR = 0x2C

# GIF versions.
GIF_87a = "87a"
GIF_89a = "89a"
VALID_GIF_REVS = [
    GIF_87a,
    GIF_89a
]


# Formatting helpers
def _yesno(pred: bool) -> str:
    return "yes" if pred else "no"


def _sortyesno(is_sorted: bool) -> str:
    return "sorted" if is_sorted else "unsorted"


def _print_colortable(
    table: Colortable,
    title: str = "Local Color Table",
    verbose: bool = False
) -> None:
    """
    Prints a color table.
    """
    print("-- {}".format(title))

    if verbose:
        for (r, g, b) in table:
            print("    ({}, {}, {})".format(r, g, b))
    else:
        print("    (omitting {} entries because no --verbose)".format(len(table)))


# Built-in formats for __str__ implementations of control blocks
GLOBAL_COLORTABLE_TEMPLATE = """present, {colortable_size} colors, {sort}
    background index: {bg}"""

LOCAL_COLORTABLE_TEMPLATE = """present, {colortable_size} colors, {sort}"""

SCREEN_DESCRIPTOR_TEMPLATE = """
-- Logical Screen Descriptor
screen size:        {d.width}x{d.height}
pixel aspect ratio: {real_pixel_aspect_ratio}
color resolution:   {d.color_resolution}
global colortable:  {colortable_string}"""

IMAGE_DESCRIPTOR_TEMPLATE = """
-- Image Descriptor
image coords:     {d.width}x{d.height}@({d.leftpos}, {d.toppos})
interlaced:       {yesno_interlaced}
local colortable: {colortable_string}"""

GRAPHIC_CONTROL_EXTENSION_TEMPLATE = """
-- Graphic Control Extension Block
disposal method: {disposal_method}
delay time:      {delay_ms}
transparency:    {transparency_string}
user input flag: {yesno_userinput}"""

GIF_FORMAT = """{path}:
screen size: {g.screen_width}x{g.screen_height}
color table: {colortable_str}
"""


class LogicalScreenDescriptor:
    """
    Model of the logical screen descriptor. Controls the size of
    the image, BG color, and global color table properties.

    This block is required, and will be available in all GIF versions.
    """
    def __init__(self):
        self.width = 0
        self.height = 0
        self.colortable_exists = False
        self.color_resolution = 0
        self.colortable_is_sorted = False
        self.colortable_size = 0
        self.background_color_index = 0
        self.pixel_aspect_ratio = 0

    def num_colors(self) -> int:
        return 2 ** (self.colortable_size + 1)

    def pretty_print(self) -> None:
        if self.colortable_exists:
            colortable_string = GLOBAL_COLORTABLE_TEMPLATE.format(
                colortable_size=self.num_colors(),
                sort=_sortyesno(self.colortable_is_sorted),
                bg=self.background_color_index)
        else:
            colortable_string = "absent"

        print(SCREEN_DESCRIPTOR_TEMPLATE.format(
            d=self,
            real_pixel_aspect_ratio=self.pixel_aspect_ratio,
            colortable_string=colortable_string))


class ImageDescriptor:
    """
    Model of an image descriptor. Controls position and size of the image, and local color table properties.

    There is exactly one image descriptor per image in a GIF. Available in all GIF versions.
    """
    def __init__(self):
        self.leftpos = 0
        self.toppos = 0
        self.width = 0
        self.height = 0

        self.interlace = False

        # local colortable info
        self.colortable_exists = False
        self.colortable_is_sorted = False
        self.colortable_size = 0

    def num_colors(self) -> int:
        return 2 ** (self.colortable_size + 1)

    def pretty_print(self) -> None:
        if self.colortable_exists:
            colortable_string = LOCAL_COLORTABLE_TEMPLATE.format(
                colortable_size=self.num_colors(),
                sort=_sortyesno(self.colortable_is_sorted))
        else:
            colortable_string = "absent"

        print(IMAGE_DESCRIPTOR_TEMPLATE.format(
            d=self,
            yesno_interlaced=_yesno(self.interlace),
            colortable_string=colortable_string))


class GraphicControlExtension:
    """
    Model of a graphic control extension block. This contains control parameters for animation. There is one
    graphic control block per image. GIF89a only. May not be present even in GIF89a.

    Note that this means each frame gets its own transparency, delay, and disposal method, which can greatly
    complicate processing depending on what you want to do.
    """
    def __init__(self):
        self.disposal_method = DisposalMethod.NONE
        self.user_input_flag = False
        self.transparent_flag = False
        self.transparent_color = 0
        self.delay = 0  # specified in 1/100ths of a second

    def delay_ms(self) -> int:
        return self.delay * 10

    def pretty_print(self) -> None:
        transparency_string = _yesno(self.transparent_flag)

        if self.transparent_flag:
            transparency_string += " (index {})".format(self.transparent_color)

        print(GRAPHIC_CONTROL_EXTENSION_TEMPLATE.format(
            disposal_method=self.disposal_method.name,
            delay_ms=self.delay_ms(),
            transparency_string=transparency_string,
            yesno_userinput=_yesno(self.user_input_flag)))


class GifStreamException(Exception):
    """
    Raised on errors parsing a GIF file.
    """
    pass


class _BlockType(Enum):
    """
    Internal block type enum. Used by _GifStream to signal what type of block is next in the stream.
    """
    IMAGE_DATA = 0
    EXT_GRAPHIC_CONTROL = 1
    EXT_COMMENT = 2
    EXT_PLAINTEXT = 3
    EXT_APPLICATION = 4
    EXT_UNKNOWN = 5
    TRAILER = 6


class _GifStream:
    """
    Internal utility class that streams along a GIF file and returns the higher level block models.
    """
    def __init__(self, path):
        # gif is little endian
        self.stream = MmapCursor(path, byteorder="little")

    def consume_header(self) -> GifVersion:
        """
        Consume the GIF header, and return the GIF revision. Also validates signature and version.
        """
        signature = self.stream.next_ascii(3)

        if signature != "GIF":
            raise GifStreamException("Bad signature " + signature)

        version = self.stream.next_ascii(3)

        if version not in VALID_GIF_REVS:
            raise GifStreamException("Invalid GIF version " + version)

        return {
            GIF_87a: GifVersion.GIF87a,
            GIF_89a: GifVersion.GIF89a
        }[version]

    def consume_screen_descriptor(self) -> LogicalScreenDescriptor:
        """
        Consume and return the required logical screen descriptor block. If desc.colortable_exists is True,
        it's expected the color table will be consumed next, with consume_color_table().
        """
        desc = LogicalScreenDescriptor()
        stream = self.stream

        desc.width = stream.next_int(2)
        desc.height = stream.next_int(2)
        packed_fields = stream.next_byte()
        desc.background_color_index = stream.next_byte()
        desc.pixel_aspect_ratio = stream.next_byte()

        # parse packed fields
        desc.colortable_size = packed_fields & 0x7
        desc.colortable_is_sorted = bool((packed_fields >> 3) & 0x1)
        desc.color_resolution = (packed_fields >> 4) & 0x7
        desc.colortable_exists = bool((packed_fields >> 7) & 0x1)

        return desc

    def consume_color_table(self, num_colors: int) -> Colortable:
        """
        Consume and return a color table. Works for both global and local tables.

        For global color tables, num_colors = LogicalScreenDescriptor.num_colors().
        For local color tables, num_colors = ImageDescriptor.num_colors()
        """
        colortable = []
        for _ in range(num_colors):
            r, g, b = self.stream.next(3)
            colortable.append((r, g, b))

        return colortable

    # consume an image descriptor and return it.
    # if desc.colortable_exists, it's expected that it is parsed next.
    def consume_image_descriptor(self) -> ImageDescriptor:
        """
        Consume and return an image descriptor. If desc.colortable_exists is True,
        it's expected the color table will be consumed next, with consume_color_table().
        """
        desc = ImageDescriptor()
        stream = self.stream

        separator = stream.next_byte()
        if separator != IMAGE_SEPARATOR:
            msg = "could not read image descriptor: bad separator {:02X}"
            raise GifStreamException(msg.format(separator))

        desc.leftpos = stream.next_int(2)
        desc.toppos = stream.next_int(2)
        desc.width = stream.next_int(2)
        desc.height = stream.next_int(2)

        packed_fields = stream.next_byte()

        # parse packed fields
        desc.colortable_size = packed_fields & 0x7
        reserved = (packed_fields >> 3) & 0x3  # ignore, but put in for consistency
        desc.colortable_is_sorted = bool((packed_fields >> 5) & 0x1)
        desc.interlaced = bool((packed_fields >> 6) & 0x1)
        desc.colortable_exists = bool((packed_fields >> 7) & 0x1)

        return desc

    def check_blocktype(self) -> _BlockType:
        """
        Determine what type of block the cursor is pointing at. If this returns BlockType.TRAILER, we're at the end.
        """
        signature = self.stream.read(2)

        if signature[0] == TRAILER_LABEL:
            return _BlockType.TRAILER

        # other than trailer, we should always get two bytes.
        if len(signature) < 2:
            msg = "Unexpected end of file while checking next block type"
            raise GifStreamException(msg)

        if signature[0] == IMAGE_SEPARATOR:
            return _BlockType.IMAGE_DATA

        if signature[0] == EXT_INTRODUCER:
            if signature[1] == EXT_GRAPHIC_CONTROL_LABEL:
                return _BlockType.EXT_GRAPHIC_CONTROL

            return _BlockType.EXT_UNKNOWN

        msg = "fatal: Unknown block with signature {:02X} {:02X}"
        raise GifStreamException(msg.format(signature[0], signature[1]))

    def skip_data(self) -> int:
        """
        Skip data sub blocks. Image data is stored as a series of blocks, each starting with a size byte, and followed
        by at most 255 bytes of data. The stream must be pointing at the beginning of a series of data blocks.

        A zero-length data block is used to terminate a series of data blocks. We stop skipping once we hit one.
        The cursor will be over the start of whatever the next block is.

        See 15. Data Sub-blocks. in GIF89a spec.

        Returns the number of data bytes skipped, not including the size bytes.
        """
        data_size = self.stream.next_byte()  # get block data size
        total_data = data_size
        # end when we get to a block size of 0
        while data_size != 0:
            self.stream.position += data_size
            data_size = self.stream.next_byte()
            total_data += data_size

        return total_data

    def skip_image_data(self) -> int:
        """
        Skip image data. Image data is just a byte describing LZW code size, and a series of data blocks.

        Returns the number of image data bytes skipped, not including metadata.
        """
        self.stream.position += 1  # skip LZW minimum code size
        return self.skip_data()

    def skip_extension(self) -> int:
        """
        Skip an extension. This is done for anything unsupported.

        - All extension blocks consist of:
        - Extension Introducer
        - Extension Label
        - Data Sub-blocks...
        - Terminator block.
        """
        introducer = self.stream.next_byte()
        if introducer != EXT_INTRODUCER:
            msg = "Unexpected byte {} while trying to skip extension!"
            raise GifStreamException(msg.format(introducer))

        self.stream.position += 1  # skip label
        return self.skip_data()  # skip the data

    def consume_graphic_control_extension(self) -> GraphicControlExtension:
        """
        Consume and return the graphic control extension.
        """
        ext = GraphicControlExtension()
        stream = self.stream

        signature = stream.next(2)

        if list(signature) != [EXT_INTRODUCER, EXT_GRAPHIC_CONTROL_LABEL]:
            raise GifStreamException("Bad signature in graphic control extension!")

        stream.position += 1  # skip block size

        packed_fields = stream.next_byte()
        ext.delay = stream.next_int(2)
        ext.transparent_color = stream.next_byte()

        stream.position += 1  # skip terminator

        ext.transparent_flag = bool(packed_fields & 0x1)
        ext.user_input_flag = bool((packed_fields >> 1) & 0x1)
        ext.disposal_method = DisposalMethod((packed_fields >> 2) & 0x7)

        return ext

    def close(self):
        """Close the GIF stream and its associated resources."""
        self.stream.close()


class Gif:
    """
    A model of a GIF, containing only the metadata + color tables. Skips image data.
    """
    def __init__(self, path: str):
        self.path = path

        self.version = GifVersion.GIF87a  # will be overwritten
        self.colortable: t.Optional[Colortable] = None  # Global color table

        # Logical screen descriptor is required, but is set with parse_metadata.
        # So, use a property to maintain type safety.
        self._logical_screen_descriptor: t.Optional[LogicalScreenDescriptor] = None

        # Note, these images are just metadata. They don't load the actual image data.
        self.images: t.Sequence[GifImage] = []

        self.__parse_metadata()

    @property
    def logical_screen_descriptor(self) -> LogicalScreenDescriptor:
        return self._logical_screen_descriptor

    def __parse_metadata(self) -> None:
        gifstream = _GifStream(self.path)

        # Consume one-time header info
        self.version = gifstream.consume_header()
        self._logical_screen_descriptor = gifstream.consume_screen_descriptor()

        if self.logical_screen_descriptor.colortable_exists:
            size = self.logical_screen_descriptor.num_colors()
            self.colortable = gifstream.consume_color_table(size)
        else:
            print("warning: no global color table")

        # Graphic control state. As we scan the file, stay on the lookout
        # for control blocks, and if we find them, remember them. Then, next
        # time we find an image, stuff the control blocks into the GifImage.
        graphic_control = None

        images = []

        # Parse remaining blocks. Mostly image data and extension data.
        while True:
            blocktype = gifstream.check_blocktype()

            if blocktype == _BlockType.TRAILER:
                break
            elif blocktype == _BlockType.EXT_GRAPHIC_CONTROL:
                if graphic_control:
                    raise Exception("Two graphic control blocks for one image!")

                graphic_control = gifstream.consume_graphic_control_extension()
            elif blocktype == _BlockType.IMAGE_DATA:
                img = GifImage(gifstream, graphic_control)
                graphic_control = None
                images.append(img)
            elif blocktype == _BlockType.EXT_UNKNOWN:
                gifstream.skip_extension()
            else:
                raise Exception("Unexpected blocktype returned!")

        self.images = images

        gifstream.close()

    def name(self) -> str:
        return ".".join(os.path.basename(self.path).split(".")[:-1])

    def pretty_print(self, verbose: bool) -> None:
        print("{g.path} ({g.version}):".format(g=self))
        self.logical_screen_descriptor.pretty_print()

        if self.colortable:
            print()
            _print_colortable(self.colortable, title="Global Color Table",
                              verbose=verbose)

        for img in self.images:
            img.pretty_print(verbose)


class GifImage:
    def __init__(self, stream: _GifStream, graphic_control: t.Optional[GraphicControlExtension] = None):
        self.stream = stream

        # The graphic control extension and local color table are both optional.
        self.graphic_control = graphic_control
        self.colortable: t.Optional[Colortable] = None

        # Image descriptor is required, use property.
        self._image_descriptor: t.Optional[ImageDescriptor] = None
        self.image_data_size = 0

        self.__parse_metadata()

    @property
    def image_descriptor(self) -> ImageDescriptor:
        return self._image_descriptor

    # parse the image descriptor and optionally the local colortable.
    # skip all image data
    def __parse_metadata(self) -> None:
        self._image_descriptor = self.stream.consume_image_descriptor()

        if self.image_descriptor.colortable_exists:
            size = self.image_descriptor.num_colors()
            self.colortable = self.stream.consume_color_table(size)

        self.image_data_size = self.stream.skip_image_data()

    def pretty_print(self, verbose: bool) -> None:
        if self.graphic_control:
            self.graphic_control.pretty_print()

        self.image_descriptor.pretty_print()

        if self.colortable:
            _print_colortable(self.colortable, title="Local Color Table",
                              verbose=verbose)

        print("-- Table Based Image Data")
        print("    ({} data bytes skipped)".format(self.image_data_size))
