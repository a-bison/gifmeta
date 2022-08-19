import argparse
import os
import math

from gifmeta import Colortable, Gif
from PIL import Image, ImageDraw


VALID_MODES = [
    "info",
    "palette",
    "help"
]


def prepare_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(
        "A tool for dumping GIF metadata. Set mode with --mode/-m. Any "
        "arguments given that do not apply to the current mode will be "
        "ignored."
    ))

    parser.add_argument("--mode", "-m", type=str, choices=VALID_MODES, default="info", help=(
        "Set operation mode. Default is \"info\". Use mode \"help\" for more "
        "information on each mode."
    ))

    parser.add_argument("--path", "-i", type=str, default=None, help=(
        "The path to the GIF file to operate on."
    ))

    parser.add_argument("--verbose", "-v", action="store_true", help=(
        "Explicitly print long lists of data, which are otherwise omitted for "
        "brevity."
    ))
    parser.add_argument("--add-local", dest="add_local", action="store_true", help=(
        "Add local color tables to the output of palette mode. This will "
        "create a directory instead of a single image."
    ))

    return parser


MODE_HELP = """Available modes:
help - 
    Print this help text.

info -
    The default mode. Prints various metadata parsed from the GIF file
    passed through --path.

palette -
    Generate an image visualizing the palette of a GIF file. By default this
    only generates an image for the global color palette, but local palettes
    may be added to the output with --add-local. This will create a directory
    containing all color tables in the GIF.
"""


def mode_help() -> None:
    print(MODE_HELP)


def mode_info(gif: Gif, args: argparse.Namespace) -> None:
    gif.pretty_print(verbose=args.verbose)


def generate_palette_img(colortable: Colortable) -> Image.Image:
    palette_block_size = 25

    num_colors = len(colortable)

    h_blocks = int(math.sqrt(num_colors))
    w_blocks = h_blocks if h_blocks ** 2 == num_colors else h_blocks + 1

    # use "missing texture purple" as the background
    img = Image.new("RGB",
                    (palette_block_size * w_blocks, palette_block_size * h_blocks),
                    color=(249, 11, 243))
    imgdraw = ImageDraw.Draw(img)

    for n, color in enumerate(colortable):
        y_block = n // w_blocks
        x_block = n % w_blocks

        y = y_block * palette_block_size
        x = x_block * palette_block_size

        rect = (
            x,
            y,
            x + palette_block_size - 1,
            y + palette_block_size - 1
        )

        imgdraw.rectangle(rect, color)

    return img


def write_global_palette(gif: Gif, parser: argparse.ArgumentParser) -> None:
    if not gif.colortable:
        parser.error("GIF has no global colortable. Abort.")

    img = generate_palette_img(gif.colortable)

    output_name = gif.name() + "_palette.png"
    img.save(output_name)

    print("Palette written to {}".format(output_name))


def mode_palette(gif: Gif, parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if not args.add_local:
        write_global_palette(gif, parser)
    else:
        local_tables = [(x, img.colortable) for (x, img)
                        in enumerate(gif.images) if img.colortable is not None]

        if not local_tables:
            print("warn: no local color tables, only outputting global table.")
            write_global_palette(gif, parser)
            return

        output_dir = gif.name() + "_palette"

        try:
            os.makedirs(output_dir)
        except FileExistsError:
            msg = "{} already exists, please delete or move and try again"
            parser.error(msg.format(output_dir))

        if gif.colortable:
            global_out_name = os.path.join(output_dir, "__global.png")
            generate_palette_img(gif.colortable).save(global_out_name)
        else:
            print("warn: no global colortable")

        local_palettes = [(x, generate_palette_img(local)) for (x, local)
                          in local_tables]
        numzeros = int(math.log(len(local_palettes), 10)) + 1

        for x, local_palette in local_palettes:
            local_name = str(x).zfill(numzeros) + ".png"
            local_path = os.path.join(output_dir, local_name)

            local_palette.save(local_path)

        print("Palettes written to {}".format(output_dir))


def main() -> None:
    parser = prepare_argparser()
    args = parser.parse_args()

    if args.mode == "help":
        mode_help()
        parser.exit()

    if args.path is None:
        parser.error("Must specify --path for non-help mode.")

    gif = Gif(args.path)
    if args.mode == "info":
        mode_info(gif, args)
    elif args.mode == "palette":
        mode_palette(gif, parser, args)
    else:
        raise Exception("internal error: invalid mode")


if __name__ == "__main__":
    main()
