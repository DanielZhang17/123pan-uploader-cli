# 123Pan Uploader CLI

![123Pan](https://img.shields.io/badge/123Pan-Cloud-blue)
![Python](https://img.shields.io/badge/Python-3.6+-green)


一个实用的服务器端文件上传工具，支持
- 123云盘大文件快速上传
- 给定直链的多线程下载

A practical server-side file uploading tool that supports fast uploading of large files to 123Pan Cloud and multi-threaded downloading for direct links.


## Technical Details

- Based on [tosasitill/123pan](https://github.com/tosasitill/123pan) repository

- Custom token retrieval logic

- Multi-threaded download implementation

- Simplified command-line interface


🤗🤗管理多台服务器的checkpoint文件需要速度和快速分发，123pan不限制的上传和高速下载很契合我的需求，于是有了这个项目。

Managing checkpoint files across multiple servers requires speed and rapid distribution, 123Pan's unlimited uploads and high-speed downloads perfectly match my requirements, leading to this project.

### Requirements

```
requests>=2.25.0
tqdm>=4.50.0
```

### Basic Usage

```bash
# 直接运行进入交互模式
# Run directly to enter interactive mode
python app.py

# 直接上传指定文件或目录
# Directly upload specified file or directory
python app.py /path/to/file_or_directory
```

### Authentication

首次运行时，如果没有`123pan.txt`认证文件，程序会要求输入用户名和密码，然后自动生成认证文件。

When running for the first time, if there's no `123pan.txt` authentication file, the program will prompt for username and password, then automatically generate the authentication file.

### Interactive Mode

1. Enter file or directory path to upload
```bash
# Upload file to root directory
> /path/to/file

# Upload file with custom destination directory name
> /path/to/file -d "Custom Folder Name"

# Upload and force overwrite existing files
> /path/to/file -f

# Upload and keep both files (this is default behavior)
> /path/to/file -k

# Combined options example
> /path/to/directory -d "My Backups" -f
```
2. Use `mget` command to download files: `mget <url> [-o output_file] [-t thread_count] [-s]`
```bash
# Starting download: https://your-direct-link → output_filename (using 16 threads)
> mget https://your-direct-link -o output_filename -t 16
```
3. Exit the program
```bash
# Type '0' to properly exit the program
> 0

# Note: Ctrl+C will only interrupt the current operation, not exit the program
```

### Command Line Arguments

Command-line arguments mirror the options available in interactive mode. You can use `-f` to force overwrite, `-k` to keep both files, and `-d` to specify a custom destination directory name.

```bash
# Combined example with all parameters
python app.py /path/to/directory -d "My Backups" -f
```


#### 此工具专为在训练机器学习模型时不打断程序的同时快速上传checkpoint而设计。

#### This tool is specially designed for uploading checkpoints quickly without interrupting running ML programs.

## Credits & Disclaimer

[tosasitill_123pan](https://github.com/tosasitill/123pan): Provides core authentication and API functionality

#### 🤔本项目仅供个人使用，与123云盘官方无关。请遵守123云盘的服务条款。

#### This project is for personal use only and is not affiliated with 123Pan Cloud. Please comply with 123Pan Cloud's terms of service.