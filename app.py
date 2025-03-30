#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import hashlib
import json
import argparse
import shlex

import requests
from tqdm import tqdm

from tosasitill_123pan.class123 import Pan123
from tosasitill_123pan.sign_get import getSign
from utils.mget import MGet
from utils.mpush import MPush, format_size


def parse_mget_command(cmd_args):
    """Parse command line arguments for file download"""
    parser = argparse.ArgumentParser(description="File Downloader")
    parser.add_argument("url", help="URL of the file to download")
    parser.add_argument(
        "-o", "--output", help="Output file path", default="downloaded_file"
    )
    parser.add_argument(
        "-t", "--threads", type=int, help="Number of threads", default=8
    )
    parser.add_argument(
        "-s", "--single", action="store_true", help="Use single-threaded download"
    )

    try:
        args = parser.parse_args(cmd_args)
        return args
    except SystemExit:
        return None


def handle_mget_command(cmd):
    """Process file download request"""
    args = shlex.split(cmd)[1:]

    if not args:
        print(
            "Error: Missing download URL. Usage: mget <url> [-o output_filename] [-t thread_count] [-s]"
        )
        return

    parsed_args = parse_mget_command(args)
    if not parsed_args:
        return

    url = parsed_args.url
    output = parsed_args.output
    threads = parsed_args.threads
    single_thread = parsed_args.single

    print(f"Starting download: {url}")
    print(f"Saving to: {output}")

    try:
        downloader = MGet(default_threads=threads)
        start_time = time.time()
        downloader.download(url, output, threads, force_single=single_thread)
        elapsed_time = time.time() - start_time
        file_size = os.path.getsize(output)

        print(f"Download complete! File size: {format_size(file_size)}")
        print(f"Time elapsed: {elapsed_time:.2f} seconds")
    except Exception as e:
        print(f"Download failed: {str(e)}")


def _mpush(mpush, path, sure_option=None, dest_name=None):
    """Upload a file or directory to 123Pan Cloud

    Args:
        mpush: MPush instance
        path: Path to file or directory to upload
        sure_option: How to handle duplicates - 1:keep both, 2:overwrite
        dest_name: Custom name for the destination directory in 123Pan
    """
    if os.path.isfile(path):
        if dest_name:
            # Create custom directory for file
            folder_id = mpush.pan.mkdir(dest_name, remake=False)
            if folder_id:
                mpush.upload_file(path, parent_id=folder_id, sure=sure_option)
                mpush.pan.cd("/")  # Return to root directory after operation
            else:
                print(f"Error: Failed to create directory '{dest_name}'")
        else:
            mpush.upload_file(path, sure=sure_option)
    elif os.path.isdir(path):
        if dest_name:
            mpush.upload_directory_concurrent(
                path, sure=sure_option, custom_dirname=dest_name
            )
        else:
            mpush.upload_directory_concurrent(path, sure=sure_option)
        mpush.pan.cd("/")  # Reset to root directory after directory upload
    else:
        print(f"Error: {path} is not a valid file or directory")


def main():
    """Main function to handle command line arguments and interactive mode"""
    parser = argparse.ArgumentParser(description="123Pan Cloud Upload Tool")
    parser.add_argument("path", nargs="?", help="Path to file or directory to upload")
    parser.add_argument(
        "-f", "--force", action="store_true", help="Overwrite files with the same name"
    )
    parser.add_argument(
        "-k", "--keep", action="store_true", help="Keep both files when names conflict"
    )
    parser.add_argument(
        "-d", "--dest", help="Specify a custom directory name in 123Pan"
    )

    args = parser.parse_args()

    # Set conflict handling strategy
    if args.force:
        sure_option = "2"  # Overwrite
    else:
        sure_option = "1"  # Default to keep both (even if -k is not specified)

    print("Logging in to 123Pan Cloud...")
    try:
        pan = Pan123(readfile=True, input_pwd=True)
        mpush = MPush(pan)
        print("Login successful!")

        # Handle command line mode
        if args.path:
            path = args.path.strip("\"'")
            if os.path.exists(path):
                _mpush(mpush, path, sure_option, args.dest)
                return
            else:
                print(f"Error: Path {path} does not exist")
                return

        # Interactive mode instructions
        print("Enter the path of file or directory to upload, or 0 to exit")
        print(
            "Or use mget <url> [-o output_filename] [-t thread_count] [-s] to download files"
        )
        print('You can also use -d "name" to specify a custom directory name in 123Pan')
        print('Example: /path/to/file -d "My Custom Directory"')
    except Exception as e:
        print(f"Login failed: {str(e)}")
        sys.exit(1)

    # Interactive mode loop
    while True:
        try:
            user_input = input("\033[91m >\033[0m ")

            if user_input == "0":
                print("Exiting program")
                break

            if user_input.startswith("mget "):
                handle_mget_command(user_input)
                continue

            # Parse command with parameters if present
            if " -" in user_input:
                parsed_args = parser.parse_args(shlex.split(user_input))
                if not parsed_args:
                    print("Invalid command format. Try again.")
                    continue

                path = parsed_args.path.strip("\"'")

                # Determine conflict handling option
                cmd_sure_option = sure_option
                if parsed_args.force:
                    cmd_sure_option = "2"  # Overwrite
                elif parsed_args.keep:
                    cmd_sure_option = "1"  # Keep both

                dest_name = parsed_args.dest
            else:
                # Simple path mode
                path = user_input.strip("\"'")
                cmd_sure_option = sure_option
                dest_name = None

            if not os.path.exists(path):
                print(f"Error: Path {path} does not exist")
                continue

            print(f"Uploading: {cmd_sure_option}")
            _mpush(mpush, path, cmd_sure_option, dest_name)

        except KeyboardInterrupt:
            print("\nOperation interrupted by user")
            continue
        except Exception as e:
            print(f"An error occurred: {str(e)}")
            continue


if __name__ == "__main__":
    main()
