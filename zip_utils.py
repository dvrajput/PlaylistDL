import os
import zipfile
import shutil
import logging
import asyncio
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger(__name__)

async def create_zip_file(files, user_id, playlist_title):
    """Create a zip file from a list of files"""
    try:
        # Create a folder for the zip file if it doesn't exist
        zip_folder = f"downloads/{user_id}/zip"
        if not os.path.exists(zip_folder):
            os.makedirs(zip_folder)
        
        # Create a safe filename for the zip
        safe_title = "".join([c if c.isalnum() or c in [' ', '-', '_'] else '_' for c in playlist_title])
        zip_filename = f"{zip_folder}/{safe_title}.zip"
        
        # Create the zip file
        with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file in files:
                if os.path.exists(file):
                    # Add file to zip with just the basename to avoid folder structure in zip
                    zipf.write(file, os.path.basename(file))
        
        return zip_filename
    except Exception as e:
        logger.error(f"Error creating zip file: {str(e)}")
        return None

async def upload_zip_to_telegram(app, user_id, zip_file, playlist_title, message, progress_callback=None):
    """Upload a zip file to Telegram"""
    try:
        # Add cancel button to the status message
        cancel_button = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel Process", callback_data="cancel_process")]
        ])
        
        await message.edit_text(
            f"📤 Uploading ZIP: {playlist_title}\n"
            f"Preparing to upload...",
            reply_markup=cancel_button
        )
        
        # Create a new message for progress tracking
        progress_message = await app.send_message(
            user_id,
            f"Starting upload of ZIP file: {os.path.basename(zip_file)}"
        )
        
        # Start time for progress
        start_time = asyncio.get_event_loop().time()
        
        # Upload with progress
        await app.send_document(
            user_id,
            zip_file,
            caption=f"Playlist: {playlist_title} (ZIP Archive)",
            progress=progress_callback,
            progress_args=(progress_message, start_time, "upload", os.path.basename(zip_file), playlist_title, 1, 1)
        )
        
        # Delete progress message after upload
        await progress_message.delete()
        
        await message.edit_text(
            f"✅ ZIP Upload completed!\n"
            f"Playlist: {playlist_title}"
        )
        
        return True
    except Exception as e:
        logger.error(f"Error uploading zip to Telegram: {str(e)}")
        await message.edit_text(
            f"❌ Failed to upload ZIP file: {str(e)}"
        )
        return False

async def upload_zip_to_gofile(zip_file, message, playlist_title, upload_to_gofile_func):
    """Upload a zip file to GoFile"""
    try:
        # Upload the zip file to GoFile
        result = await upload_to_gofile_func(zip_file, message, f"{playlist_title} (ZIP)")
        
        if result and "downloadPage" in result:
            await message.edit_text(
                f"✅ ZIP Upload to GoFile completed!\n"
                f"Playlist: {playlist_title}\n\n"
                f"Download link: {result['downloadPage']}"
            )
            return True
        else:
            await message.edit_text(
                f"❌ Failed to upload ZIP file to GoFile."
            )
            return False
    except Exception as e:
        logger.error(f"Error uploading zip to GoFile: {str(e)}")
        await message.edit_text(
            f"❌ Failed to upload ZIP file to GoFile: {str(e)}"
        )
        return False