"""``family-memories`` — the command line entry point.

Groups are added here as each project is built. Right now that is the
contribution kit; the vault, ingest, and heritage groups follow.
"""

from __future__ import annotations

import sys
from contextlib import suppress

import click

from contribute.cli import contribute
from enrich.cli import enrich_group
from index.cli import index_group
from ingest.cli import ingest_group
from vault.cli import vault_group


def _force_utf8_output() -> None:
    """Make console output UTF-8 safe.

    Reports contain typographic characters, and person tags contain whatever
    the contributor typed — accents, emoji, non-Latin scripts. On a legacy
    Windows console those raise UnicodeEncodeError mid-print, which would
    crash a run partway through rather than mangling a dash.
    """
    for stream in (sys.stdout, sys.stderr):
        with suppress(AttributeError, OSError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")


@click.group()
@click.version_option(package_name="family-memories")
def main() -> None:
    """Consolidate a family's photos into a permanent searchable archive."""
    _force_utf8_output()


main.add_command(contribute)
main.add_command(index_group)
main.add_command(vault_group)
main.add_command(ingest_group)
main.add_command(enrich_group)


if __name__ == "__main__":
    main()
