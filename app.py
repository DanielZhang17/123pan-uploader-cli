#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import hashlib
import json
import requests
import argparse
import shlex
from tqdm import tqdm

from tosasitill_123pan.class123 import Pan123
from tosasitill_123pan.sign_get import getSign
from utils.mget import download_single_thread, download_multi_thread


def format_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes/1024:.2f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes/(1024*1024):.2f} MB"
    else:
        return f"{size_bytes/(1024*1024*1024):.2f} GB"


def compute_file_md5(file_path):
    with open(file_path, "rb") as f:
        md5 = hashlib.md5()
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            md5.update(chunk)
        return md5.hexdigest()


def upload_file(pan, file_path, parent_id=None, sure="1"):
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        print(f"Error: {file_path} is not a valid file")
        return False

    file_path = file_path.replace('"', "").replace("\\", "/")
    file_name = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)

    print(f"Preparing to upload: {file_name} ({format_size(file_size)})")

    print("Calculating file MD5...")
    md5 = compute_file_md5(file_path)

    if parent_id is None:
        parent_id = pan.parentFileId

    list_up_request = {
        "driveId": 0,
        "etag": md5,
        "fileName": file_name,
        "parentFileId": parent_id,
        "size": file_size,
        "type": 0,
        "duplicate": 0,
    }

    sign = getSign("/b/api/file/upload_request")
    up_res = requests.post(
        "https://www.123pan.com/b/api/file/upload_request",
        headers=pan.headerLogined,
        params={sign[0]: sign[1]},
        data=json.dumps(list_up_request),
    )

    up_res_json = up_res.json()
    code = up_res_json.get("code")

    if code == 5060:
        print("Duplicate file detected")
        if sure not in ["1", "2"]:
            sure = input(
                "Duplicate file detected. Enter 1 to overwrite, 2 to keep both, 0 to cancel: "
            )

        if sure == "1":
            list_up_request["duplicate"] = 1
        elif sure == "2":
            list_up_request["duplicate"] = 2
        else:
            print("Upload cancelled")
            return False

        sign = getSign("/b/api/file/upload_request")
        up_res = requests.post(
            "https://www.123pan.com/b/api/file/upload_request",
            headers=pan.headerLogined,
            params={sign[0]: sign[1]},
            data=json.dumps(list_up_request),
        )
        up_res_json = up_res.json()
        code = up_res_json.get("code")

    if code == 0:
        reuse = up_res_json["data"].get("Reuse")
        if reuse:
            print(f"Upload successful, file MD5 reused: {file_name}")
            return True
    else:
        print(f"Upload request failed: {up_res_json}")
        return False

    bucket = up_res_json["data"]["Bucket"]
    storage_node = up_res_json["data"]["StorageNode"]
    upload_key = up_res_json["data"]["Key"]
    upload_id = up_res_json["data"]["UploadId"]
    up_file_id = up_res_json["data"]["FileId"]

    start_data = {
        "bucket": bucket,
        "key": upload_key,
        "uploadId": upload_id,
        "storageNode": storage_node,
    }

    start_res = requests.post(
        "https://www.123pan.com/b/api/file/s3_list_upload_parts",
        headers=pan.headerLogined,
        data=json.dumps(start_data),
    )

    start_res_json = start_res.json()
    if start_res_json["code"] != 0:
        print(f"Failed to get transfer list: {start_res_json}")
        return False

    block_size = 5242880  # 5MB
    part_number_start = 1

    with open(file_path, "rb") as f, tqdm(
        total=file_size, unit="B", unit_scale=True, desc=file_name
    ) as pbar:
        while True:
            data = f.read(block_size)
            if not data:
                break

            get_link_data = {
                "bucket": bucket,
                "key": upload_key,
                "partNumberEnd": part_number_start + 1,
                "partNumberStart": part_number_start,
                "uploadId": upload_id,
                "StorageNode": storage_node,
            }

            get_link_res = requests.post(
                "https://www.123pan.com/b/api/file/s3_repare_upload_parts_batch",
                headers=pan.headerLogined,
                data=json.dumps(get_link_data),
            )

            get_link_res_json = get_link_res.json()
            if get_link_res_json["code"] != 0:
                print(f"Failed to get upload link: {get_link_res_json}")
                return False

            upload_url = get_link_res_json["data"]["presignedUrls"][
                str(part_number_start)
            ]
            requests.put(upload_url, data=data)

            pbar.update(len(data))
            part_number_start += 1

    print("Chunk upload complete, finalizing...")

    uploaded_list_url = "https://www.123pan.com/b/api/file/s3_list_upload_parts"
    uploaded_comp_data = {
        "bucket": bucket,
        "key": upload_key,
        "uploadId": upload_id,
        "storageNode": storage_node,
    }

    requests.post(
        uploaded_list_url,
        headers=pan.headerLogined,
        data=json.dumps(uploaded_comp_data),
    )

    comp_multipart_up_url = (
        "https://www.123pan.com/b/api/file/s3_complete_multipart_upload"
    )
    requests.post(
        comp_multipart_up_url,
        headers=pan.headerLogined,
        data=json.dumps(uploaded_comp_data),
    )

    if file_size > 64 * 1024 * 1024:
        time.sleep(3)

    close_up_session_url = "https://www.123pan.com/b/api/file/upload_complete"
    close_up_session_data = {"fileId": up_file_id}

    close_up_session_res = requests.post(
        close_up_session_url,
        headers=pan.headerLogined,
        data=json.dumps(close_up_session_data),
    )

    close_res_json = close_up_session_res.json()
    if close_res_json["code"] == 0:
        print(f"Upload successful: {file_name}")
        return True
    else:
        print(f"Upload failed: {close_res_json}")
        return False


def upload_directory(pan, dir_path, parent_id=None):
    if not os.path.isdir(dir_path):
        print(f"Error: {dir_path} is not a valid directory")
        return False

    dir_name = os.path.basename(dir_path)
    print(f"Preparing to upload directory: {dir_name}")

    folder_id = pan.mkdir(dir_name, parent_id, remake=False)
    if not folder_id:
        print(f"Failed to create directory {dir_name}")
        return False

    print(f"Directory created: {dir_name}, ID: {folder_id}")

    success_count = 0
    fail_count = 0

    for item in os.listdir(dir_path):
        item_path = os.path.join(dir_path, item)

        if os.path.isfile(item_path):
            if upload_file(pan, item_path, folder_id):
                success_count += 1
            else:
                fail_count += 1

        elif os.path.isdir(item_path):
            if upload_directory(pan, item_path, folder_id):
                success_count += 1
            else:
                fail_count += 1

    print(
        f"Directory {dir_name} upload complete: {success_count} successful, {fail_count} failed"
    )
    return True


def parse_mget_command(cmd_args):
    parser = argparse.ArgumentParser(
        description="File Downloader - Single and Multi-threaded"
    )
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
        start_time = time.time()

        if single_thread:
            print("Using single-threaded download mode")
            download_single_thread(url, output)
        else:
            print(f"Using multi-threaded download mode (threads: {threads})")
            download_multi_thread(url, output, threads)

        elapsed_time = time.time() - start_time
        file_size = os.path.getsize(output)

        print(f"Download complete! File size: {format_size(file_size)}")
        print(f"Time elapsed: {elapsed_time:.2f} seconds")

    except Exception as e:
        print(f"Download failed: {str(e)}")


def main():
    parser = argparse.ArgumentParser(description="123Pan Cloud Upload Tool")
    parser.add_argument("path", nargs="?", help="Path to file or directory to upload")
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Automatically overwrite files with the same name",
    )
    parser.add_argument(
        "-k", "--keep", action="store_true", help="Keep both files when names conflict"
    )

    args = parser.parse_args()

    if args.force:
        sure_option = "1"  # Overwrite
    elif args.keep:
        sure_option = "2"  # Keep both
    else:
        sure_option = "1"  # Default to overwrite

    print("Logging in to 123Pan Cloud...")
    try:
        pan = Pan123(readfile=True, input_pwd=True)
        print("Login successful!")

        if args.path:
            path = args.path.strip("\"'")
            if os.path.exists(path):
                if os.path.isfile(path):
                    upload_file(pan, path, sure=sure_option)
                elif os.path.isdir(path):
                    upload_directory(pan, path)
                else:
                    print(f"Error: {path} is not a valid file or directory")
                return
            else:
                print(f"Error: Path {path} does not exist")
                return

        print("Enter the path of file or directory to upload, or 0 to exit")
        print(
            "Or use mget <url> [-o output_filename] [-t thread_count] [-s] to download files"
        )
    except Exception as e:
        print(f"Login failed: {str(e)}")
        sys.exit(1)

    while True:
        try:
            user_input = input("\033[91m >\033[0m ")

            if user_input == "0":
                print("Exiting program")
                break

            if user_input.startswith("mget "):
                handle_mget_command(user_input)
                continue

            path = user_input.strip("\"'")

            if not os.path.exists(path):
                print(f"Error: Path {path} does not exist")
                continue

            if os.path.isfile(path):
                upload_file(pan, path)
            elif os.path.isdir(path):
                upload_directory(pan, path)
            else:
                print(f"Error: {path} is not a valid file or directory")
        except KeyboardInterrupt:
            print("\nOperation interrupted by user")
            continue
        except Exception as e:
            print(f"An error occurred: {str(e)}")
            continue


if __name__ == "__main__":
    main()
