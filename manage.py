#!/usr/bin/env python
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fuelroute.settings")
    # This is the development command line, so debugging is on unless the
    # environment says otherwise. A server importing wsgi.py gets the
    # production default instead.
    os.environ.setdefault("DJANGO_DEBUG", "1")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
