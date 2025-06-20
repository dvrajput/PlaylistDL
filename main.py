import yt_dlp
import os
import shutil
import time
import subprocess
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, LinkPreviewOptions
import logging
import asyncio
from zip_utils import create_zip_file, upload_zip_to_telegram, upload_zip_to_gofile
import requests
from requests_toolbelt import MultipartEncoder, MultipartEncoderMonitor
from config import Config

# Disable pyrogram logging
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logging.getLogger("pyrogram.client").setLevel(logging.WARNING)
logging.getLogger("pyrogram.session.session").setLevel(logging.WARNING)

# Configure logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Pyrogram client
api_id = Config.API_ID
api_hash = Config.API_HASH
bot_token = Config.BOT_TOKEN

app = Client("playlist_dl_bot", api_id=api_id, api_hash=api_hash, bot_token=bot_token)

# Store user selections
user_data = {}
# Track progress updates
last_progress_update = {}
# Track cancelled uploads
upload_cancelled = {}
active_processes = {}
authorized_users = set()
# Owner ID from config
OWNER_ID = Config.OWNER_ID

# Load authorized users from file
def load_authorized_users():
    auth_file = "authorized_users.txt"
    if os.path.exists(auth_file):
        with open(auth_file, "r") as f:
            for line in f:
                user_id = line.strip()
                if user_id.isdigit():
                    authorized_users.add(int(user_id))
    logger.info(f"Loaded {len(authorized_users)} authorized users")

# Save authorized users to file
def save_authorized_users():
    auth_file = "authorized_users.txt"
    with open(auth_file, "w") as f:
        for user_id in authorized_users:
            f.write(f"{user_id}\n")
    logger.info(f"Saved {len(authorized_users)} authorized users")

# Check if user is authorized
def is_authorized(user_id):
    return user_id == OWNER_ID or user_id in authorized_users

def format_size(size_bytes):
    """Format size in bytes to human readable format"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes/1024:.2f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes/(1024*1024):.2f} MB"
    else:
        return f"{size_bytes/(1024*1024*1024):.2f} GB"

def format_time(seconds):
    """Format seconds to human readable time"""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds//60}m {seconds%60:.0f}s"
    else:
        return f"{seconds//3600}h {(seconds%3600)//60}m {seconds%3600%60:.0f}s"

def create_download_folder(user_id):
    """Create a folder for downloads if it doesn't exist"""
    download_path = f"downloads/{user_id}"
    if not os.path.exists(download_path):
        os.makedirs(download_path)
    return download_path

# Add this function to check file size
def check_file_size(file_path):
    """Check if file size exceeds Telegram's limit"""
    file_size = os.path.getsize(file_path)
    # 2GB limit for regular users (slightly less to be safe)
    return file_size > 1.9 * 1024 * 1024 * 1024

# Add this function to split large videos
async def split_video(file_path, user_id, message):
    """Split large video into smaller parts"""
    base_name = os.path.basename(file_path)
    name_without_ext, extension = os.path.splitext(base_name)
    output_dir = f"downloads/{user_id}/split"
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    # Maximum size for each part (1.8GB)
    max_part_size = 1.8 * 1024 * 1024 * 1024
    file_size = os.path.getsize(file_path)
    
    # Calculate number of parts needed
    num_parts = (file_size + max_part_size - 1) // max_part_size
    
    await message.edit_text(f"File {base_name} is too large for Telegram. Splitting into {num_parts} parts...")
    
    try:
        # Check if it's a video file
        is_video = False
        try:
            # Use ffprobe to check if it's a video and get duration
            cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', file_path]
            duration = float(subprocess.check_output(cmd).decode('utf-8').strip())
            is_video = True
        except:
            is_video = False
        
        split_files = []
        
        if is_video:
            # For video files, split by duration to achieve desired file sizes
            # Calculate time segments based on file size ratio
            segment_duration = duration / (file_size / max_part_size)
            
            start_time = 0
            i = 1
            
            while start_time < duration:
                parted_name = f"{name_without_ext}.part{i:03}{extension}"
                out_path = os.path.join(output_dir, parted_name)
                
                # For the last part, use the remaining duration
                if i == num_parts:
                    # Use the remaining duration for the last part
                    end_time = duration
                else:
                    # Calculate end time for this segment
                    end_time = min(start_time + segment_duration, duration)
                
                segment_length = end_time - start_time
                
                # Use ffmpeg to split by time segments
                cmd = [
                    'ffmpeg', '-hide_banner', '-loglevel', 'error', 
                    '-ss', str(start_time), 
                    '-i', file_path,
                    '-t', str(segment_length),
                    '-c', 'copy', 
                    '-avoid_negative_ts', '1',
                    out_path
                ]
                
                process = await asyncio.create_subprocess_exec(
                    *cmd, 
                    stderr=asyncio.subprocess.PIPE
                )
                
                code = await process.wait()
                
                if code != 0:
                    err = (await process.stderr.read()).decode().strip()
                    logger.error(f"Error splitting video: {err}")
                    
                    # Try without some options if it failed
                    cmd = [
                        'ffmpeg', '-hide_banner', '-loglevel', 'error', 
                        '-ss', str(start_time), 
                        '-i', file_path,
                        '-t', str(segment_length),
                        '-c', 'copy', 
                        out_path
                    ]
                    
                    process = await asyncio.create_subprocess_exec(
                        *cmd, 
                        stderr=asyncio.subprocess.PIPE
                    )
                    
                    code = await process.wait()
                    
                    if code != 0:
                        err = (await process.stderr.read()).decode().strip()
                        logger.error(f"Error splitting video (second attempt): {err}")
                        return None
                
                # Check if the output file exists and has a valid size
                if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    split_files.append(out_path)
                    
                    # Update progress
                    await message.edit_text(f"Splitting {base_name}: Part {i}/{num_parts} completed ({format_size(os.path.getsize(out_path))})")
                
                # Move to next segment
                start_time = end_time
                i += 1
                
                # Break if we've created all needed parts
                if i > num_parts:
                    break
        else:
            # For non-video files, use the split command with exact byte sizes
            # This will create parts of max_part_size bytes, except for the last part which will be smaller
            out_path = os.path.join(output_dir, f"{name_without_ext}.")
            
            # Use the split command for non-video files
            cmd = [
                'split', '--numeric-suffixes=1', '--suffix-length=3',
                f'--bytes={int(max_part_size)}', file_path, out_path
            ]
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stderr=asyncio.subprocess.PIPE
            )
            
            code = await process.wait()
            
            if code != 0:
                err = (await process.stderr.read()).decode().strip()
                logger.error(f"Error splitting file: {err}")
                return None
            
            # Get list of split files
            split_files = sorted([
                os.path.join(output_dir, f) 
                for f in os.listdir(output_dir)
                if os.path.isfile(os.path.join(output_dir, f))
            ])
            
            # Update progress for each part
            for i, part_file in enumerate(split_files, 1):
                part_size = os.path.getsize(part_file)
                await message.edit_text(f"Split {base_name}: Part {i}/{len(split_files)} ({format_size(part_size)})")
        
        return split_files
    except Exception as e:
        logger.error(f"Error splitting file: {str(e)}")
        return None

async def progress(current, total, message, start_time, operation, filename=None, playlist_title=None, file_index=None, total_files=None):
    """Generic progress callback for uploads/downloads"""
    try:
        now = time.time()
        elapsed_time = now - start_time
        
        last_update = last_progress_update.get(message.id, 0)
        
        # Update only every 5 seconds or when complete
        if current == total or (now - last_update) >= 5:
            speed = current / elapsed_time if elapsed_time > 0 else 0
            percentage = (current * 100) / total if total > 0 else 0
            filled = int(percentage / 10)
            bar = '█' * filled + '░' * (10 - filled)
            eta = (total - current) / speed if speed > 0 else 0
            # Convert speed to Mbps instead of bytes/s
            speed_mbps = speed / (1024 * 1024) * 8  # Convert bytes/s to Mbps

            try:
                if operation == "upload" and playlist_title and filename and file_index is not None and total_files is not None:
                    progress_text = (
                        f"📤 Uploading: {playlist_title}\n"
                        f"File {file_index}/{total_files} \n\n"
                        f"📝 Title: {filename}\n"
                        f"{bar} {percentage:.1f}%\n\n"
                        f"⌛ Size: {format_size(current)}/{format_size(total)}\n"
                        f"⚡️ Speed: {format_size(speed)}/s\n"
                        f"⏰ ETA: {format_time(eta)}"
                    )
                else:
                    progress_text = (
                        f"{'📤 Uploading' if operation == 'upload' else '📥 Downloading'}\n"
                        f"{bar} {percentage:.1f}%\n\n"
                        f"⌛ Size: {format_size(current)}/{format_size(total)}\n"
                        f"⚡️ Speed: {format_size(speed)}/s\n"
                        f"⏰ ETA: {format_time(eta)}"
                    )
                
                # Store the last message text to compare
                last_text = getattr(message, '_last_progress_text', '')
                
                # Only update if the text has changed
                if progress_text != last_text:
                    await message.edit_text(progress_text)
                    # Store the new text
                    message._last_progress_text = progress_text
                
                # Update last progress time for this message
                last_progress_update[message.id] = now
                
                # Check if upload was cancelled
                if upload_cancelled.get(message.id, False):
                    raise asyncio.CancelledError("Upload cancelled by user")
            except asyncio.CancelledError:
                raise
            except pyrogram.errors.exceptions.bad_request_400.MessageNotModified:
                # Ignore this specific error
                pass
            except Exception as e:
                print(f"Progress update error: {str(e)}")
            
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"Progress callback error: {str(e)}")

def get_video_info(url):
    """Get playlist information"""
    ydl_opts = {
        'quiet': True,
        'cookiefile': 'cookies.txt',
        'no_warnings': True,
        'format': 'best',
        'outtmpl': '%(title)s.%(ext)s',
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            result = ydl.extract_info(url, download=False)
            return result
        except Exception as e:
            logger.error(f"Error getting playlist info: {str(e)}")
            return None

def download_video(video_url, download_path, quality):
    """Download a single video with specified quality"""
    format_string = {
        '144': 'bestvideo[height<=144]+bestaudio/best[height<=144]',
        '240': 'bestvideo[height<=240]+bestaudio/best[height<=240]',
        '360': 'bestvideo[height<=360]+bestaudio/best[height<=360]',
        '480': 'bestvideo[height<=480]+bestaudio/best[height<=480]',
        '720': 'bestvideo[height<=720]+bestaudio/best[height<=720]',
        '1080': 'bestvideo[height<=1080]+bestaudio/best[height<=1080]',
        '2160': 'bestvideo[height<=2160]+bestaudio/best[height<=2160]'
    }
    
    # Debug log to verify the quality parameter
    logger.info(f"Downloading video with quality: {quality}")
    
    ydl_opts = {
        'format': format_string.get(quality, 'bestvideo+bestaudio/best'),
        'outtmpl': os.path.join(download_path, '%(title)s.%(ext)s'),
        'cookiefile': 'cookies.txt',
        'merge_output_format': 'mp4',
        'ignoreerrors': True,
        'no_warnings': True,
        'quiet': True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            ydl.download([video_url])
            info = ydl.extract_info(video_url, download=False)
            filename = ydl.prepare_filename(info)
            return filename
        except Exception as e:
            logger.error(f"Error downloading video: {str(e)}")
            return None

def download_audio(video_url, download_path, format_type):
    """Download a single video as audio with specified format (mp3 or wav)"""
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': os.path.join(download_path, '%(title)s.%(ext)s'),
        'cookiefile': 'cookies.txt',
        'ignoreerrors': True,
        'no_warnings': True,
        'quiet': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': format_type,
            'preferredquality': '192',
        }],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            ydl.download([video_url])
            info = ydl.extract_info(video_url, download=False)
            filename = ydl.prepare_filename(info)
            # Change extension to match the requested format
            filename = os.path.splitext(filename)[0] + f".{format_type}"
            return filename
        except Exception as e:
            logger.error(f"Error downloading audio: {str(e)}")
            return None

async def download_playlist(url, user_id, quality, message):
    """Download videos from playlist with specified quality"""
    download_path = create_download_folder(user_id)
    
    # Debug log to verify the quality parameter
    logger.info(f"Starting playlist download with quality: {quality}")
    
    playlist_info = get_video_info(url)
    if not playlist_info:
        await message.edit_text("Failed to get playlist information.")
        return False

    playlist_title = playlist_info.get('title', 'Playlist')
    total_videos = len(playlist_info['entries'])
    
    await message.edit_text(
        f"📥 Downloading: {playlist_title}\n"
        f"Total videos: {total_videos}\n"
        f"Selected quality: {quality}p\n\n"
        f"0/{total_videos} completed"
    )

    downloaded_files = []
    
    for i, entry in enumerate(playlist_info['entries'], 1):
        # Check if process was cancelled
        if active_processes.get(user_id, {}).get("cancelled", False):
            await message.edit_text("Process cancelled by user.")
            # Clean up downloaded files
            cleanup_path = f"downloads/{user_id}"
            if os.path.exists(cleanup_path):
                shutil.rmtree(cleanup_path)
            return False
            
        if entry:
            video_title = entry.get('title', f'Video {i}')
            video_url = entry['webpage_url']
            
            await message.edit_text(
                f"📥 Downloading: {playlist_title}\n"
                f"Total videos: {total_videos}\n"
                f"Selected quality: {quality}p\n\n"
                f"Downloading {i}/{total_videos}: {video_title}"
            )
            
            filename = download_video(video_url, download_path, quality)
            if filename:
                downloaded_files.append(filename)
            
            await message.edit_text(
                f"📥 Downloading: {playlist_title}\n"
                f"Total videos: {total_videos}\n"
                f"Selected quality: {quality}p\n\n"
                f"{i}/{total_videos} completed"
            )

    # Show upload options after download is complete
    if downloaded_files:
        # Create keyboard with upload options including ZIP option
        upload_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📤 Upload to Telegram", callback_data=f"upload_telegram_{user_id}"),
                InlineKeyboardButton("☁️ Upload to GoFile", callback_data=f"upload_gofile_{user_id}")
            ],
            [
                InlineKeyboardButton("🗜️ Upload as ZIP", callback_data=f"toggle_zip_{user_id}_off")
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_process")]
        ])
        
        await message.edit_text(
            f"✅ Download completed!\n"
            f"Playlist: {playlist_title}\n"
            f"Total files: {len(downloaded_files)}\n\n"
            f"Please select where to upload:\n"
            f"ZIP Mode: Off",
            reply_markup=upload_keyboard
        )
        
        # Store download info for later use
        user_data[user_id]['files'] = downloaded_files
        user_data[user_id]['playlist_title'] = playlist_title
        user_data[user_id]['zip_mode'] = False
        
        return True
    else:
        await message.edit_text("Download failed. No files were downloaded.")
        return False

async def upload_videos_to_telegram(user_id, files, playlist_title, message):
    """Upload downloaded videos to Telegram"""
    # Add cancel button to the status message
    cancel_button = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel Process", callback_data="cancel_process")]
    ])
    
    # Check if cover image exists
    thumbnail_path = "covers/cover1.jpg"
    has_thumbnail = os.path.exists(thumbnail_path)

    await message.edit_text(
        f"✅ Download completed!\n"
        f"Playlist: {playlist_title}\n"
        f"Total files: {len(files)}\n\n"
        f"Uploading to Telegram...",
        reply_markup=cancel_button
    )
    
    # Check if we're dealing with audio files
    is_audio = user_data.get(user_id, {}).get('is_audio', False)
    
    for i, file_path in enumerate(files, 1):
        # Check if process was cancelled
        if active_processes.get(user_id, {}).get("cancelled", False):
            await message.edit_text("Process cancelled by user.")
            # Clean up downloaded files
            cleanup_path = f"downloads/{user_id}"
            if os.path.exists(cleanup_path):
                shutil.rmtree(cleanup_path)
            return
            
        try:
            filename = os.path.basename(file_path)
            
            # Update main status message for current file
            await message.edit_text(
                f"📤 Uploading: {playlist_title}\n"
                f"File {i}/{len(files)}: {filename}\n\n"
                f"Processing...",
                reply_markup=cancel_button
            )
            
                        # Check if file is too large
            if check_file_size(file_path):
                # For large files, handle differently based on type
                if is_audio:
                    # For audio, we'll just upload as document since splitting audio is less common
                    await message.edit_text(
                        f"📤 Uploading: {playlist_title}\n"
                        f"File {i}/{len(files)}: {filename}\n\n"
                        f"File is large, uploading as document...",
                        reply_markup=cancel_button
                    )
                    
                    # Create a new message for progress tracking
                    progress_message = await app.send_message(
                        user_id,
                        f"Starting upload: {filename}"
                    )
                    
                    # Start time for progress
                    start_time = time.time()
                    
                    # Upload with progress
                    await app.send_document(
                        user_id,
                        file_path,
                        caption=f"{filename}\n\nFrom playlist: {playlist_title}",
                        thumb=thumbnail_path if has_thumbnail else None,
                        progress=progress,
                        progress_args=(progress_message, start_time, "upload", filename, playlist_title, i, len(files))
                    )
                    
                    # Delete progress message after upload
                    await progress_message.delete()
                else:
                    # For videos, use the existing split video function
                    # Save the original message text to restore later
                    original_status = f"📤 Uploading: {playlist_title}\n" \
                                    f"File {i}/{len(files)}: {filename}\n\n"
                    
                    # Split the video into parts
                    split_files = await split_video(file_path, user_id, message)
                    
                    if not split_files:
                        await app.send_message(user_id, f"Failed to split large file: {filename}")
                        continue
                    
                    # Restore original status message with additional info
                    await message.edit_text(
                        f"{original_status}"
                        f"Uploading {len(split_files)} split parts...",
                        reply_markup=cancel_button
                    )
                    
                    # Upload each part
                    for part_index, part_file in enumerate(split_files, 1):
                        part_filename = os.path.basename(part_file)
                        
                        # Update main status with part info
                        await message.edit_text(
                            f"{original_status}"
                            f"Uploading part {part_index}/{len(split_files)}...",
                            reply_markup=cancel_button
                        )
                        
                        # Get video duration if possible
                        try:
                            duration_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', part_file]
                            duration = int(float(subprocess.check_output(duration_cmd).decode('utf-8').strip()))
                        except:
                            duration = 0
                        
                        # Create a new message for progress tracking
                        progress_message = await app.send_message(
                            user_id,
                            f"Starting upload: {part_filename} (Part {part_index}/{len(split_files)})"
                        )
                        
                        # Start time for progress
                        start_time = time.time()
                        
                        # Upload with progress
                        await app.send_video(
                            user_id,
                            part_file,
                            caption=f"{filename} - Part {part_index}/{len(split_files)}\n\nFrom playlist: {playlist_title}",
                            supports_streaming=True,
                            duration=duration,
                            thumb=thumbnail_path if has_thumbnail else None,
                            progress=progress,
                            progress_args=(progress_message, start_time, "upload", part_filename, playlist_title, i, len(files))
                        )
                        
                        # Delete progress message after upload
                        await progress_message.delete()

                        # Add 4-second delay between uploads to avoid flood wait
                        if part_index < len(split_files):
                            await asyncio.sleep(Config.UPLOAD_INTERVAL)
                    
                    # Update status after all parts are uploaded
                    await message.edit_text(
                        f"📤 Uploading: {playlist_title}\n"
                        f"File {i}/{len(files)}: {filename}\n\n"
                        f"✅ All {len(split_files)} parts uploaded successfully!",
                        reply_markup=cancel_button
                    )
            else:
                # Regular upload for normal sized files
                # Get audio duration if possible
                try:
                    duration_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', file_path]
                    duration = int(float(subprocess.check_output(duration_cmd).decode('utf-8').strip()))
                except:
                    duration = 0
                
                # Create a new message for progress tracking
                progress_message = await app.send_message(
                    user_id,
                    f"Starting upload: {filename}"
                )
                
                # Start time for progress
                start_time = time.time()
                
                # Upload with progress - for audio files use send_audio instead of send_video
                if is_audio:
                    await app.send_audio(
                        user_id,
                        file_path,
                        caption=f"{filename}\n\nFrom playlist: {playlist_title}",
                        duration=duration,
                        thumb=thumbnail_path if has_thumbnail else None,
                        progress=progress,
                        progress_args=(progress_message, start_time, "upload", filename, playlist_title, i, len(files))
                    )
                else:
                    await app.send_video(
                        user_id,
                        file_path,
                        caption=f"{filename}\n\nFrom playlist: {playlist_title}",
                        supports_streaming=True,
                        duration=duration,
                        thumb=thumbnail_path if has_thumbnail else None,
                        progress=progress,
                        progress_args=(progress_message, start_time, "upload", filename, playlist_title, i, len(files))
                    )
                
                # Delete progress message after upload
                await progress_message.delete()
                
                # Update main status message
                await message.edit_text(
                    f"📤 Uploading: {playlist_title}\n"
                    f"File {i}/{len(files)}: {filename}\n\n"
                    f"✅ Uploaded successfully!",
                    reply_markup=cancel_button
                )

            # Add 4-second delay between uploads to avoid flood wait
            if i < len(files):
                await asyncio.sleep(Config.UPLOAD_INTERVAL)
            
        except Exception as e:
            logger.error(f"Error uploading file {file_path}: {str(e)}")
            await app.send_message(user_id, f"Failed to upload {filename}: {str(e)}")

            # If we get a flood wait error, add an extra delay
            if "FLOOD_WAIT" in str(e):
                wait_time = 20  # Default wait time if we can't extract the exact time
                try:
                    # Try to extract the wait time from the error message
                    import re
                    wait_match = re.search(r'A wait of (\d+) seconds', str(e))
                    if wait_match:
                        wait_time = int(wait_match.group(1)) + 2  # Add 2 seconds as buffer
                except:
                    pass
                
                logger.info(f"Got FLOOD_WAIT, waiting for {wait_time} seconds")
                await message.edit_text(
                    f"Rate limit hit. Waiting for {wait_time} seconds before continuing...",
                    reply_markup=cancel_button
                )
                await asyncio.sleep(wait_time)

    # Remove user from active processes
    active_processes.pop(user_id, None)
    
    # Clean up split files directory if it exists
    split_dir = f"downloads/{user_id}/split"
    if os.path.exists(split_dir):
        shutil.rmtree(split_dir)
    
    await message.edit_text(
        f"✅ Process completed!\n"
        f"Playlist: {playlist_title}\n"
        f"All {len(files)} videos have been uploaded."
    )
    # Log successful Telegram upload
    try:
        user = await app.get_users(user_id)
        user_mention = f"@{user.username}" if user.username else f"[{user.first_name}](tg://user?id={user_id})"
        
        # Get the original URL from user_data
        original_url = user_data.get(user_id, {}).get('url', 'Unknown URL')
        
        log_message = (
            "#PlaylistBotLogs \n"
            f"✅ Telegram upload completed!\n"
            f"👤 User: {user_mention}\n"
            f"🆔 ID: `{user_id}`\n"
            f"📋 Playlist: {playlist_title}\n"
            f"📁 Files: {len(files)}\n"
            f"🔗 YouTube URL: {original_url}"
        )
        await send_log(log_message)
    except Exception as e:
        logger.error(f"Failed to send upload completion log: {str(e)}")

async def upload_to_gofile(file_path, message, current_video_title, folder_id=None):
    """Upload a file to GoFile"""
    try:
        server_url = "https://api.gofile.io/servers"
        server_response = requests.get(server_url)
        
        if server_response.status_code != 200:
            raise Exception(f"Failed to get server. Status code: {server_response.status_code}")
        
        server_data = server_response.json()
        server = server_data["data"]["servers"][0]["name"]

        upload_url = f"https://{server}.gofile.io/uploadFile"

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        
        token = Config.GOFILE_TOKEN

        file_size = os.path.getsize(file_path)
        start_time = time.time()
        last_update_time = start_time
        update_interval = 5  # Reduced to 5 seconds for more frequent updates
        last_progress_text = ""  # Track the last progress text to avoid duplicate updates

        # Progress callback function with improved error handling
        def progress_callback(monitor):
            nonlocal last_update_time, last_progress_text
            current_time = time.time()
            
            if current_time - last_update_time >= update_interval:
                try:
                    percentage = (monitor.bytes_read * 100) / monitor.len
                    filled = int(percentage / 10)
                    bar = '█' * filled + '░' * (10 - filled)
                    speed = monitor.bytes_read / (current_time - start_time)
                    
                    # Format sizes
                    current_mb = monitor.bytes_read / (1024 * 1024)
                    total_mb = monitor.len / (1024 * 1024)
                    speed_mb = speed / (1024 * 1024)
                    
                    # Calculate ETA
                    remaining_bytes = monitor.len - monitor.bytes_read
                    eta = remaining_bytes / speed if speed > 0 else 0
                    
                    progress_text = (
                        f"📤 Uploading to Gofile...\n\n"
                        f"📁 File: {current_video_title}\n"
                        f"{bar} {percentage:.1f}%\n\n"
                        f"⌛ Uploaded: {current_mb:.1f}/{total_mb:.1f} MB\n"
                        f"⚡️ Speed: {speed_mb:.2f} MB/s\n"
                        f"⏰ ETA: {format_time(eta)}"
                    )

                    # Only update if the text has changed
                    if progress_text != last_progress_text:
                        # Add cancel button
                        cancel_button = {
                            "reply_markup": {
                                "inline_keyboard": [[{
                                    "text": "❌ Cancel",
                                    "callback_data": f"cancel_{message.id}"
                                }]]
                            }
                        }
                        
                        try:
                            requests.post(
                                f"https://api.telegram.org/bot{Config.BOT_TOKEN}/editMessageText",
                                json={
                                    "chat_id": message.chat.id,
                                    "message_id": message.id,
                                    "text": progress_text,
                                    "reply_markup": cancel_button["reply_markup"]
                                }
                            )
                            last_progress_text = progress_text  # Update the last text
                        except Exception as e:
                            # Just log the error but don't stop the upload
                            print(f"Failed to update progress: {str(e)}")
                            
                    # Check if upload was cancelled
                    if upload_cancelled.get(message.id, False):
                        print("Upload cancelled by user")
                        try:
                            if os.path.exists(file_path):
                                os.remove(file_path)
                        except Exception as e:
                            print(f"Error cleaning up file: {str(e)}")
                        # Raise StopIteration to actually stop the upload
                        raise StopIteration("Upload cancelled by user")

                    last_update_time = current_time
                except StopIteration:
                    raise
                except Exception as e:
                    print(f"Progress update error: {str(e)}")

        try:
            safe_filename = os.path.basename(file_path).encode('ascii', 'ignore').decode('ascii')
            # Include folderId in the upload if provided
            fields = {
                'file': (safe_filename, open(file_path, 'rb'), 'application/octet-stream')
            }
            
            # Add folder ID if provided
            if folder_id:
                fields['folderId'] = (None, folder_id)
                
            encoder = MultipartEncoder(fields=fields)
            monitor = MultipartEncoderMonitor(encoder, callback=progress_callback)

            try:
                response = requests.post(
                    upload_url,
                    data=monitor,
                    headers={'Content-Type': monitor.content_type,
                            'Authorization': f"Bearer {token}"},
                    timeout=3600
                )
            except StopIteration:
                print("Upload stopped by user")
                return None

            # Check if cancelled after upload
            if upload_cancelled.get(message.id, False):
                print("Upload cancelled by user")
                return None
                
            if response.status_code != 200:
                raise Exception(f"Upload failed with status code: {response.status_code}, Response: {response.text}")

            result = response.json()
            
            if result["status"] == "ok" and isinstance(result["data"], dict):
                return result["data"]
            else:
                print(f"Unexpected response format: {result}")
                return None

        except Exception as e:
            if "Upload cancelled by user" in str(e):
                return None
            raise
    except Exception as e:
        print(f"GoFile upload error: {str(e)}")
        return None
    finally:
        try:
            if 'encoder' in locals() and hasattr(encoder.fields['file'][1], 'close'):
                encoder.fields['file'][1].close()
        except:
            pass

async def create_gofile_folder(name, token):
    """Create a folder in GoFile"""
    try:
        # First get the account ID
        account_id_url = "https://api.gofile.io/accounts/getid"
        headers = {
            "Authorization": f"Bearer {token}"
        }
        
        account_response = requests.get(account_id_url, headers=headers)
        
        if account_response.status_code != 200:
            raise Exception(f"Failed to get account ID. Status code: {account_response.status_code}, Response: {account_response.text}")
            
        account_data = account_response.json()
        
        if account_data["status"] != "ok":
            raise Exception(f"Failed to get account ID: {account_data.get('message', 'Unknown error')}")
        
        # Get the account ID
        account_id = account_data["data"]["id"]
        
        # Get the root folder ID for this account
        account_details_url = f"https://api.gofile.io/accounts/{account_id}"
        account_details_response = requests.get(account_details_url, headers=headers)
        
        if account_details_response.status_code != 200:
            raise Exception(f"Failed to get account details. Status code: {account_details_response.status_code}")
            
        account_details = account_details_response.json()
        
        if account_details["status"] != "ok":
            raise Exception(f"Failed to get account details: {account_details.get('message', 'Unknown error')}")
        
        # Get the root folder ID
        root_folder_id = account_details["data"]["rootFolder"]
        
        # Now create the folder with the root folder ID
        url = "https://api.gofile.io/contents/createFolder"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }
        data = {
            "parentFolderId": root_folder_id,
            "folderName": name
        }
        
        print(f"Creating folder with data: {data}")
        
        response = requests.post(url, headers=headers, json=data)
        
        if response.status_code != 200:
            raise Exception(f"Failed to create folder. Status code: {response.status_code}, Response: {response.text}")
            
        result = response.json()
        
        if result["status"] == "ok":
            return result["data"]["id"]
        else:
            raise Exception(f"Failed to create folder: {result.get('message', 'Unknown error')}")
    except Exception as e:
        print(f"Error creating GoFile folder: {str(e)}")
        return None

async def upload_files_to_gofile(user_id, files, playlist_title, message):
    """Upload all downloaded files to GoFile"""
    # Add cancel button to the status message
    cancel_button = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel Process", callback_data="cancel_process")]
    ])
    
    await message.edit_text(
        f"✅ Preparing to upload to GoFile!\n"
        f"Playlist: {playlist_title}\n"
        f"Total files: {len(files)}\n\n"
        f"Creating folder...",
        reply_markup=cancel_button
    )
    
    # Create a folder for the playlist
    token = Config.GOFILE_TOKEN
    folder_id = await create_gofile_folder(playlist_title, token)
    
    if not folder_id:
        await message.edit_text(
            f"❌ Failed to create folder on GoFile.\n"
            f"Please try again later."
        )
        return
    
    # Track uploaded files and their links
    uploaded_files = []
    folder_link = None
    
    for i, file_path in enumerate(files, 1):
        # Check if process was cancelled
        if active_processes.get(user_id, {}).get("cancelled", False):
            await message.edit_text("Process cancelled by user.")
            # Clean up downloaded files
            cleanup_path = f"downloads/{user_id}"
            if os.path.exists(cleanup_path):
                shutil.rmtree(cleanup_path)
            return
            
        try:
            filename = os.path.basename(file_path)
            
            # Update status message
            await message.edit_text(
                f"📤 Uploading to GoFile: {playlist_title}\n"
                f"File {i}/{len(files)}: {filename}\n\n"
                f"Starting upload...",
                reply_markup=cancel_button
            )
            
            # Upload file to GoFile with folder ID
            result = await upload_to_gofile(file_path, message, filename, folder_id)
            
            if result:
                uploaded_files.append(filename)
                # Store folder link from the first successful upload
                if not folder_link and "parentFolder" in result:
                    # Fix: Check if parentFolder is a dictionary and has directLink
                    if isinstance(result["parentFolder"], dict) and "directLink" in result["parentFolder"]:
                        folder_link = result["parentFolder"]["directLink"]
                    # If parentFolder is a string (folder ID) or doesn't have directLink
                    elif "parentFolderCode" in result:
                        folder_link = f"https://gofile.io/d/{result['parentFolderCode']}"
                
                # Update status message
                await message.edit_text(
                    f"📤 Uploading to GoFile: {playlist_title}\n"
                    f"File {i}/{len(files)}: {filename}\n\n"
                    f"✅ Upload successful!",
                    reply_markup=cancel_button
                )
            else:
                await message.edit_text(
                    f"📤 Uploading to GoFile: {playlist_title}\n"
                    f"File {i}/{len(files)}: {filename}\n\n"
                    f"❌ Upload failed.",
                    reply_markup=cancel_button
                )
            # Add 4-second delay between uploads to avoid rate limits
            if i < len(files):
                await asyncio.sleep(Config.UPLOAD_INTERVAL)
        
        except Exception as e:
            logger.error(f"Error uploading file to GoFile {file_path}: {str(e)}")
            await app.send_message(user_id, f"Failed to upload {filename} to GoFile: {str(e)}")
    
    # Remove user from active processes
    active_processes.pop(user_id, None)
    
    # Clean up downloaded files
    cleanup_path = f"downloads/{user_id}"
    if os.path.exists(cleanup_path):
        shutil.rmtree(cleanup_path)
    
    # Final message with folder link
    if not folder_link and len(uploaded_files) > 0 and "parentFolderCode" in result:
        # Fallback to using parentFolderCode if we couldn't get directLink
        folder_link = f"https://gofile.io/d/{result['parentFolderCode']}"
        
    if folder_link and uploaded_files:
        await message.edit_text(
            f"✅ GoFile upload completed!\n"
            f"Playlist: {playlist_title}\n"
            f"Total files uploaded: {len(uploaded_files)}/{len(files)}\n\n"
            f"Download link (all files in one folder):\n{folder_link}"
        )
         # Log successful GoFile upload
        try:
            user = await app.get_users(user_id)
            user_mention = f"@{user.username}" if user.username else f"[{user.first_name}](tg://user?id={user_id})"
            
            # Get the original URL from user_data
            original_url = user_data.get(user_id, {}).get('url', 'Unknown URL')
            
            log_message = (
                "#PlaylistBotLogs \n"
                f"✅ GoFile upload completed!\n"
                f"👤 User: {user_mention}\n"
                f"🆔 ID: `{user_id}`\n"
                f"📋 Playlist: {playlist_title}\n"
                f"📁 Files: {len(uploaded_files)}/{len(files)}\n"
                f"🔗 YouTube URL: {original_url}\n"
                f"📥 GoFile Link: {folder_link}"
            )
            await send_log(log_message)
        except Exception as e:
            logger.error(f"Failed to send upload completion log: {str(e)}")
    else:
        await message.edit_text(
            f"❌ GoFile upload failed!\n"
            f"Playlist: {playlist_title}\n"
            f"No files were uploaded successfully."
        )
        # Log failed upload
        try:
            user = await app.get_users(user_id)
            user_mention = f"@{user.username}" if user.username else f"[{user.first_name}](tg://user?id={user_id})"
            log_message = (
                "#PlaylistBotLogs \n"
                f"❌ GoFile upload failed!\n"
                f"👤 User: {user_mention}\n"
                f"🆔 ID: `{user_id}`\n"
                f"📋 Playlist: {playlist_title}"
            )
            await send_log(log_message)
        except Exception as e:
            logger.error(f"Failed to send upload failure log: {str(e)}")

@app.on_callback_query(filters.regex(r'^cancel_process$'))
async def cancel_process(client, callback_query):
    user_id = callback_query.from_user.id
    
    if user_id in active_processes:
        active_processes[user_id]["cancelled"] = True
        # Immediately update the message to show cancellation
        await callback_query.message.edit_text("Process cancelled by user.")
        
        # Clean up downloaded files
        cleanup_path = f"downloads/{user_id}"
        if os.path.exists(cleanup_path):
            shutil.rmtree(cleanup_path)
            
        # Remove from active processes
        active_processes.pop(user_id, None)
        
        await callback_query.answer("Process cancelled successfully.")
    else:
        await callback_query.answer("No active process to cancel.")

@app.on_message(filters.command("start"))
async def start_command(client, message):
    user_id = message.from_user.id
    
    if is_authorized(user_id):
        await message.reply_text(
            "Welcome to YouTube Playlist Downloader Bot!\n\n"
            "Send me a YouTube playlist URL and I'll help you download it."
        )
    else:
        # Create an inline keyboard with admin contact link
        admin_button = InlineKeyboardMarkup([
            [InlineKeyboardButton("Contact Admin", url=f"https://t.me/{Config.ADMIN_USERNAME}")]
        ])
         # Log unauthorized access attempt
        user_mention = f"@{message.from_user.username}" if message.from_user.username else f"[{message.from_user.first_name}](tg://user?id={user_id})"
        log_message = "#PlaylistBotLogs \n" f"⚠️ Unauthorized access attempt!\n👤 User: {user_mention}\n🆔 ID: `{user_id}`"
        await send_log(log_message)
        
        await message.reply_text(
            "You are not authorized to use this bot.\n"
            "Please contact the bot owner for authorization.",
            reply_markup=admin_button,
            link_preview_options=LinkPreviewOptions(is_disabled=True)
        )

@app.on_message(filters.regex(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'))
async def handle_url(client, message):
    url = message.text.strip()
    user_id = message.from_user.id

     # Check if user is authorized
    if not is_authorized(user_id):
        # Create an inline keyboard with admin contact link
        admin_button = InlineKeyboardMarkup([
            [InlineKeyboardButton("Contact Admin", url=f"https://t.me/{Config.ADMIN_USERNAME}")]
        ])

        # Log unauthorized access attempt
        user_mention = f"@{message.from_user.username}" if message.from_user.username else f"[{message.from_user.first_name}](tg://user?id={user_id})"
        log_message = "#PlaylistBotLogs \n" f"⚠️ Unauthorized URL request!\n👤 User: {user_mention}\n🆔 ID: `{user_id}`\n🔗 URL: {url}"
        await send_log(log_message)
        
        await message.reply_text(
            "You are not authorized to use this bot.\n"
            "Please contact the bot owner for authorization.",
            reply_markup=admin_button,
            link_preview_options=LinkPreviewOptions(is_disabled=True)
        )
        return

    # Check if user already has an active process
    if user_id in active_processes and not active_processes[user_id].get("cancelled", False):
        await message.reply_text(
            "⚠️ You already have an active download process.\n"
            "Please wait for it to complete or cancel it before starting a new one."
        )
        return

    status_message = await message.reply_text("Checking Playlist URL, it'll take some time \n ⌛ Please Wait...")
    # Store the message ID for potential cancellation
    active_processes[user_id] = {"status_message_id": status_message.id, "cancelled": False}
    
    playlist_info = get_video_info(url)
    if not playlist_info:
        await status_message.edit_text("Invalid URL or couldn't fetch playlist information.")
        active_processes.pop(user_id, None)
        return
    
    user_data[user_id] = {'url': url}
    
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎵 MP3", callback_data="format_mp3"),
            InlineKeyboardButton("🎵 WAV", callback_data="format_wav")
        ],
        [
            InlineKeyboardButton("144p", callback_data="quality_144"),
            InlineKeyboardButton("240p", callback_data="quality_240")
        ],
        [
            InlineKeyboardButton("360p", callback_data="quality_360"),
            InlineKeyboardButton("480p", callback_data="quality_480")
        ],
        [
            InlineKeyboardButton("720p", callback_data="quality_720"),
            InlineKeyboardButton("1080p", callback_data="quality_1080")
        ],
        [
            InlineKeyboardButton("2160p (4K)", callback_data="quality_2160")
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_process")]
    ])
    
    playlist_title = playlist_info.get('title', 'Unknown Playlist')
    total_videos = len(playlist_info['entries'])
    
    user_mention = f"@{message.from_user.username}" if message.from_user.username else f"[{message.from_user.first_name}](tg://user?id={user_id})"
    log_message = (
        "#PlaylistBotLogs \n"
        f"🚀 New download task started!\n"
        f"👤 User: {user_mention}\n"
        f"🆔 ID: `{user_id}`\n"
        f"📋 Playlist: {playlist_title}\n"
        f"📊 Videos: {total_videos}\n"
        f"🔗 URL: {url}"
    )
    await send_log(log_message)

    await status_message.edit_text(
        f"📋 Playlist: {playlist_title}\n"
        f"📊 Total videos: {total_videos}\n\n"
        f"Please select download quality:",
        reply_markup=keyboard
    )

@app.on_callback_query(filters.regex(r'^format_'))
async def handle_format_selection(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    data = callback_query.data
    
    # Check if it's a format selection
    if data.startswith("format_"):
        format_type = data.split('_')[1]
        
        if user_id not in user_data:
            await callback_query.answer("Session expired. Please send the URL again.")
            return
        
        url = user_data[user_id]['url']
        user_data[user_id]['format_type'] = format_type
        
        # Make sure user is in active processes
        if user_id not in active_processes:
            active_processes[user_id] = {"status_message_id": callback_query.message.id, "cancelled": False}
        
        await callback_query.message.edit_text(
            f"Starting download process with {format_type.upper()} audio format..."
        )
        
        # Download the playlist as audio
        result = await download_playlist_audio(url, user_id, format_type, callback_query.message)
        
        # If download failed or was cancelled
        if not result:
            if not active_processes.get(user_id, {}).get("cancelled", False):
                await callback_query.message.edit_text("Download failed. Please try again.")
            active_processes.pop(user_id, None)

async def download_playlist_audio(url, user_id, format_type, message):
    """Download videos from playlist as audio files with specified format"""
    download_path = create_download_folder(user_id)
    
    playlist_info = get_video_info(url)
    if not playlist_info:
        await message.edit_text("Failed to get playlist information.")
        return False

    playlist_title = playlist_info.get('title', 'Playlist')
    total_videos = len(playlist_info['entries'])
    
    await message.edit_text(
        f"📥 Downloading: {playlist_title}\n"
        f"Total tracks: {total_videos}\n"
        f"Selected format: {format_type.upper()}\n\n"
        f"0/{total_videos} completed"
    )

    downloaded_files = []
    
    for i, entry in enumerate(playlist_info['entries'], 1):
        # Check if process was cancelled
        if active_processes.get(user_id, {}).get("cancelled", False):
            await message.edit_text("Process cancelled by user.")
            # Clean up downloaded files
            cleanup_path = f"downloads/{user_id}"
            if os.path.exists(cleanup_path):
                shutil.rmtree(cleanup_path)
            return False
            
        if entry:
            track_title = entry.get('title', f'Track {i}')
            video_url = entry['webpage_url']
            
            await message.edit_text(
                f"📥 Downloading: {playlist_title}\n"
                f"Total tracks: {total_videos}\n"
                f"Selected format: {format_type.upper()}\n\n"
                f"Downloading {i}/{total_videos}: {track_title}"
            )
            
            filename = download_audio(video_url, download_path, format_type)
            if filename:
                downloaded_files.append(filename)
            
            await message.edit_text(
                f"📥 Downloading: {playlist_title}\n"
                f"Total tracks: {total_videos}\n"
                f"Selected format: {format_type.upper()}\n\n"
                f"{i}/{total_videos} completed"
            )

    # Show upload options after download is complete
    if downloaded_files:
        # Create keyboard with upload options including ZIP option
        upload_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📤 Upload to Telegram", callback_data=f"upload_telegram_{user_id}"),
                InlineKeyboardButton("☁️ Upload to GoFile", callback_data=f"upload_gofile_{user_id}")
            ],
            [
                InlineKeyboardButton("🗜️ Upload as ZIP", callback_data=f"toggle_zip_{user_id}_off")
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_process")]
        ])
        
        await message.edit_text(
            f"✅ Download completed!\n"
            f"Playlist: {playlist_title}\n"
            f"Total audio files: {len(downloaded_files)}\n"
            f"Format: {format_type.upper()}\n\n"
            f"Please select where to upload:\n"
            f"ZIP Mode: Off",
            reply_markup=upload_keyboard
        )
        
        # Store download info for later use
        user_data[user_id]['files'] = downloaded_files
        user_data[user_id]['playlist_title'] = f"{playlist_title} ({format_type.upper()})"
        user_data[user_id]['is_audio'] = True
        user_data[user_id]['zip_mode'] = False
        
        return True
    else:
        await message.edit_text("Download failed. No files were downloaded.")
        return False

@app.on_callback_query(filters.regex(r'^quality_'))
async def handle_quality_selection(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    data = callback_query.data
    
    # Check if it's a quality selection
    if data.startswith("quality_"):
        quality = data.split('_')[1]
        
        if user_id not in user_data:
            await callback_query.answer("Session expired. Please send the URL again.")
            return
        
        url = user_data[user_id]['url']
        user_data[user_id]['quality'] = quality
        
        # Make sure user is in active processes
        if user_id not in active_processes:
            active_processes[user_id] = {"status_message_id": callback_query.message.id, "cancelled": False}
        
        await callback_query.message.edit_text(
            f"Starting download process with {quality}p quality..."
        )
        
        # Download the playlist but don't upload yet - let user choose upload method
        result = await download_playlist(url, user_id, quality, callback_query.message)
        
        # If download failed or was cancelled
        if not result:
            if not active_processes.get(user_id, {}).get("cancelled", False):
                await callback_query.message.edit_text("Download failed. Please try again.")
            active_processes.pop(user_id, None)

@app.on_callback_query(filters.regex(r'^toggle_zip_\d+_(on|off)$'))
async def toggle_zip_mode(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    data = callback_query.data
    
    # Extract user ID and current state from callback data
    parts = data.split('_')
    target_user_id = int(parts[2])
    current_state = parts[3]  # "on" or "off"
    
    # Verify this is the correct user
    if user_id != target_user_id:
        await callback_query.answer("This is not your download.")
        return
    
    if user_id not in user_data or 'files' not in user_data[user_id]:
        await callback_query.answer("Session expired. Please start over.")
        return
    
    # Toggle the state
    new_state = "off" if current_state == "on" else "on"
    user_data[user_id]['zip_mode'] = (new_state == "on")
    
    # Get the current message text and update it
    message_text = callback_query.message.text
    if "ZIP Mode:" in message_text:
        message_text = message_text.replace(f"ZIP Mode: {current_state.capitalize()}", f"ZIP Mode: {new_state.capitalize()}")
    
    # Update the keyboard with the new toggle state
    files = user_data[user_id]['files']
    playlist_title = user_data[user_id]['playlist_title']
    
    upload_keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📤 Upload to Telegram", callback_data=f"upload_telegram_{user_id}"),
            InlineKeyboardButton("☁️ Upload to GoFile", callback_data=f"upload_gofile_{user_id}")
        ],
        [
            InlineKeyboardButton(
                f"🗜️ Upload as ZIP: {'✅ On' if new_state == 'on' else '❌ Off'}", 
                callback_data=f"toggle_zip_{user_id}_{new_state}"
            )
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_process")]
    ])
    
    await callback_query.message.edit_text(
        message_text,
        reply_markup=upload_keyboard
    )
    
    await callback_query.answer(f"ZIP mode: {new_state.upper()}")

@app.on_callback_query(filters.regex(r'^upload_(telegram|gofile)_\d+$'))
async def handle_upload_selection(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    data = callback_query.data
    
    # Extract upload type and user ID from callback data
    upload_type, target_user_id = data.split('_')[1], int(data.split('_')[2])
    
    # Verify this is the correct user
    if user_id != target_user_id:
        await callback_query.answer("This is not your download.")
        return
    
    if user_id not in user_data or 'files' not in user_data[user_id]:
        await callback_query.answer("Session expired. Please start over.")
        return
    
    files = user_data[user_id]['files']
    playlist_title = user_data[user_id]['playlist_title']
    zip_mode = user_data[user_id].get('zip_mode', False)
    
    # Make sure user is in active processes
    if user_id not in active_processes:
        active_processes[user_id] = {"status_message_id": callback_query.message.id, "cancelled": False}
    
    await callback_query.answer(f"Starting upload to {upload_type.capitalize()}...")
    
    # Handle ZIP mode if enabled
    if zip_mode:
        await callback_query.message.edit_text(
            f"Creating ZIP archive for {playlist_title}...\n"
            f"This may take some time depending on the size of the files."
        )
        
        # Create the ZIP file
        zip_file = await create_zip_file(files, user_id, playlist_title)
        
        if not zip_file:
            await callback_query.message.edit_text(
                f"❌ Failed to create ZIP archive.\n"
                f"Please try again or upload files individually."
            )
            return
        
        # Upload the ZIP file based on selected destination
        if upload_type == 'telegram':
            await upload_zip_to_telegram(app, user_id, zip_file, playlist_title, 
                                         callback_query.message, progress)
        else:  # GoFile
            await upload_zip_to_gofile(zip_file, callback_query.message, 
                                       playlist_title, upload_to_gofile)
        
        # Clean up the ZIP file after upload
        try:
            if os.path.exists(zip_file):
                os.remove(zip_file)
        except Exception as e:
            logger.error(f"Error removing ZIP file: {str(e)}")
    else:
        # Regular upload without ZIP
        if upload_type == 'telegram':
            # Use existing function for Telegram uploads
            await upload_videos_to_telegram(user_id, files, playlist_title, callback_query.message)
        else:  # GoFile
            # Use existing function for GoFile uploads
            await upload_files_to_gofile(user_id, files, playlist_title, callback_query.message)

# Then modify your existing cancel_upload function to only handle numeric IDs
@app.on_callback_query(filters.regex(r'^cancel_\d+$'))
async def cancel_upload(client, callback_query):
    message_id = int(callback_query.data.split('_')[1])
    upload_cancelled[message_id] = True
    # Immediately update the message
    await callback_query.message.edit_text("Upload cancelled by user.")
    await callback_query.answer("Upload cancelled successfully")

@app.on_message(filters.command("auth") & filters.user(OWNER_ID))
async def auth_command(client, message):
    # Check if the command has a user ID
    if len(message.command) != 2:
        await message.reply_text("Usage: /auth [user_id]")
        return
    
    try:
        user_id = int(message.command[1])
        
        # Try to get user info to include username in the response
        try:
            user = await app.get_users(user_id)
            user_mention = f"@{user.username}" if user.username else f"[{user.first_name}](tg://user?id={user_id})"
        except:
            user_mention = f"User {user_id}"
            
        if user_id in authorized_users:
            await message.reply_text(f"{user_mention} is already authorized.")
        else:
            authorized_users.add(user_id)
            save_authorized_users()
            await message.reply_text(f"{user_mention} has been authorized.")
    except ValueError:
        await message.reply_text("Invalid user ID. Please provide a valid numeric ID.")

# Modify the revoke_command function to mention users
@app.on_message(filters.command("revoke") & filters.user(OWNER_ID))
async def revoke_command(client, message):
    # Check if the command has a user ID
    if len(message.command) != 2:
        await message.reply_text("Usage: /revoke [user_id]")
        return
    
    try:
        user_id = int(message.command[1])
        
        # Try to get user info to include username in the response
        try:
            user = await app.get_users(user_id)
            user_mention = f"@{user.username}" if user.username else f"[{user.first_name}](tg://user?id={user_id})"
        except:
            user_mention = f"User {user_id}"
            
        if user_id in authorized_users:
            authorized_users.remove(user_id)
            save_authorized_users()
            await message.reply_text(f"Authorization for {user_mention} has been revoked.")
        else:
            await message.reply_text(f"{user_mention} is not in the authorized list.")
    except ValueError:
        await message.reply_text("Invalid user ID. Please provide a valid numeric ID.")

# Modify the list_auth_command function to mention users
@app.on_message(filters.command("list") & filters.user(OWNER_ID))
async def list_auth_command(client, message):
    if not authorized_users:
        await message.reply_text("No users are currently authorized.")
    else:
        # Get user info for each authorized user
        auth_list = []
        for user_id in authorized_users:
            try:
                user = await app.get_users(user_id)
                if user.username:
                    user_info = f"• {user_id} - @{user.username}"
                else:
                    user_info = f"• {user_id} - {user.first_name}"
            except:
                user_info = f"• {user_id}"
            auth_list.append(user_info)
        
        await message.reply_text(f"Authorized users:\n{chr(10).join(auth_list)}")

async def send_log(message, disable_notification=False):
    """Send log message to the configured log channel"""
    if Config.LOG_CHANNEL != 0:
        try:
            await app.send_message(
                chat_id=Config.LOG_CHANNEL,
                text=message,
                disable_notification=disable_notification,
                link_preview_options=LinkPreviewOptions(is_disabled=True)
            )
        except Exception as e:
            logger.error(f"Failed to send log message: {str(e)}")

@app.on_message(filters.command("setcookies") & filters.user(OWNER_ID))
async def set_cookies_command(client, message):
    """Set YouTube cookies from message text (Owner only)"""
    try:
        # Check if command is a reply to a message
        if not message.reply_to_message or not message.reply_to_message.text:
            await message.reply_text(
                "❌ Please reply to a message containing cookies with /setcookies command",
                quote=True
            )
            return

        cookies_content = message.reply_to_message.text.strip()
        
        # Basic validation
        if not any(domain in cookies_content.lower() for domain in ['.youtube.com', 'youtube.com']):
            await message.reply_text(
                "❌ Invalid cookies content. Must contain YouTube cookies.",
                quote=True
            )
            return

        # Write cookies to file
        try:
            with open("cookies.txt", 'w', encoding='utf-8') as f:
                f.write(cookies_content)
                
            await message.reply_text(
                "✅ Cookies updated successfully!",
                quote=True
            )
            
        except Exception as e:
            await message.reply_text(
                f"❌ Error saving cookies: {str(e)}",
                quote=True
            )
            
    except Exception as e:
        # Replace handle_error with direct error handling
        logger.error(f"Error in set_cookies_command: {str(e)}")
        await message.reply_text(
            f"❌ An error occurred: {str(e)}",
            quote=True
        )
        
if __name__ == "__main__":
    if not os.path.exists("downloads"):
        os.makedirs("downloads")
    # Load authorized users when the bot starts
    load_authorized_users()
    print("Bot is running...")
    app.run()