import yt_dlp
import os
import shutil
import time
from pyrogram import __version__
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import logging
import asyncio
from config import Config
print(__version__)

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
                
                await message.edit_text(
                    progress_text
                )
                
                # Update last progress time for this message
                last_progress_update[message.id] = now
                
                # Check if upload was cancelled
                if upload_cancelled.get(message.id, False):
                    raise asyncio.CancelledError("Upload cancelled by user")
            except asyncio.CancelledError:
                raise
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
        'cookies': 'cookies.txt',
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
    
    ydl_opts = {
        'format': format_string.get(quality, 'bestvideo+bestaudio/best'),
        'outtmpl': os.path.join(download_path, '%(title)s.%(ext)s'),
        'cookies': 'cookies.txt',
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
    
async def download_playlist(url, user_id, quality, message):
    """Download videos from playlist with specified quality"""
    download_path = create_download_folder(user_id)
    
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

    return downloaded_files, playlist_title

# Modify the upload function to check for cancellation
async def upload_videos_to_telegram(user_id, files, playlist_title, message):
    """Upload downloaded videos to Telegram"""
    # Add cancel button to the status message
    cancel_button = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel Process", callback_data="cancel_process")]
    ])
    
    # Check if cover image exists
    thumbnail_path = "covers/cover.jpg"
    has_thumbnail = os.path.exists(thumbnail_path)

    await message.edit_text(
        f"✅ Download completed!\n"
        f"Playlist: {playlist_title}\n"
        f"Total files: {len(files)}\n\n"
        f"Starting upload to Telegram...",
        reply_markup=cancel_button
    )
    
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
            
            # Get video duration if possible
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
            
            # Upload with progress
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
            
        except Exception as e:
            logger.error(f"Error uploading file {file_path}: {str(e)}")
            await app.send_message(user_id, f"Failed to upload {filename}: {str(e)}")

    # Remove user from active processes
    active_processes.pop(user_id, None)
    
    await message.edit_text(
        f"✅ Process completed!\n"
        f"Playlist: {playlist_title}\n"
        f"All {len(files)} videos have been uploaded."
    )

@app.on_message(filters.command("start"))
async def start_command(client, message):
    await message.reply_text(
        "Welcome to YouTube Playlist Downloader Bot!\n\n"
        "Send me a YouTube playlist URL and I'll help you download it."
    )

@app.on_message(filters.regex(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'))
async def handle_url(client, message):
    url = message.text.strip()
    user_id = message.from_user.id
    

    
    status_message = await message.reply_text("Checking URL...")
    
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
    
    await status_message.edit_text(
        f"📋 Playlist: {playlist_title}\n"
        f"📊 Total videos: {total_videos}\n\n"
        f"Please select download quality:",
        reply_markup=keyboard
    )

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
        
        files, playlist_title = await download_playlist(url, user_id, quality, callback_query.message)
        
        if files and not active_processes.get(user_id, {}).get("cancelled", False):
            await upload_videos_to_telegram(user_id, files, playlist_title, callback_query.message)
            cleanup_path = f"downloads/{user_id}"
            if os.path.exists(cleanup_path):
                shutil.rmtree(cleanup_path)
        elif not files:
            await callback_query.message.edit_text("Download failed. Please try again.")
            active_processes.pop(user_id, None)

# You need to add a separate callback handler for cancel_process
@app.on_callback_query(filters.regex(r'^cancel_process$'))
async def cancel_process(client, callback_query):
    user_id = callback_query.from_user.id
    
    if user_id in active_processes:
        active_processes[user_id]["cancelled"] = True
        await callback_query.answer("Process will be cancelled soon.")
    else:
        await callback_query.answer("No active process to cancel.")

# Then modify your existing cancel_upload function to only handle numeric IDs
@app.on_callback_query(filters.regex(r'^cancel_\d+$'))
async def cancel_upload(client, callback_query):
    message_id = int(callback_query.data.split('_')[1])
    upload_cancelled[message_id] = True
    await callback_query.answer("Upload will be cancelled")

if __name__ == "__main__":
    if not os.path.exists("downloads"):
        os.makedirs("downloads")
    
    print("Bot is running...")
    app.run()