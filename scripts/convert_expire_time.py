#!/usr/bin/env python3
"""Convert Unix timestamps from broker inventory to human-readable dates."""

import sys
from datetime import datetime
from pathlib import Path

def convert_timestamp(timestamp):
    """Convert Unix timestamp to human-readable format.

    Args:
        timestamp: Unix timestamp (seconds since epoch)

    Returns:
        Human-readable date string in format: YYYY-MM-DD HH:MM:SS
    """
    try:
        dt = datetime.fromtimestamp(int(timestamp))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError):
        return "Invalid timestamp"


def main():
    """Convert timestamp(s) from command line or stdin."""
    if len(sys.argv) > 1:
        # Process timestamps from command line arguments
        for timestamp in sys.argv[1:]:
            readable = convert_timestamp(timestamp)
            print(f"{timestamp} -> {readable}")
    else:
        # Process from stdin (useful for piping)
        print("Enter Unix timestamps (one per line, Ctrl+D to finish):")
        for line in sys.stdin:
            timestamp = line.strip()
            if timestamp:
                readable = convert_timestamp(timestamp)
                print(f"{timestamp} -> {readable}")


if __name__ == "__main__":
    main()
