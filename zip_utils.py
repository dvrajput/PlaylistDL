  GNU nano 7.2                                                                zip_utils.py *                                                                       
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

        await asyncio.sleep(5)
        await message.delete()
        
        # Send log message for successful upload
        try:
            # Use the app instance passed to the function instead of importing it
            user = await app.get_users(user_id)
            user_mention = f"@{user.username}" if user.username else f"[{user.first_name}](tg://user?id={user_id})"
            
            # Import only what's needed
            from main import user_data, send_log, active_processes
            original_url = user_data.get(user_id, {}).get('url', 'Unknown URL')
            
            log_message = (
                "#PlaylistBotLogs \n"
                f"✅ Telegram ZIP upload completed!\n"
                f"👤 User: {user_mention}\n"
                f"🆔 ID: `{user_id}`\n"
                f"📋 Playlist: {playlist_title}\n"
                f"🔗 YouTube URL: {original_url}"
            )
            await send_log(log_message)
            
            # Remove user from active processes
            active_processes.pop(user_id, None)
        except Exception as e:
            logger.error(f"Failed to send upload completion log: {str(e)}")
        
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
        # Extract user_id from message
        user_id = message.chat.id
        
        # Upload the zip file to GoFile
        result = await upload_to_gofile_func(zip_file, message, f"{playlist_title} (ZIP)")
        
        if result and "downloadPage" in result:
            await message.edit_text(
                f"✅ ZIP Upload to GoFile completed!\n"
                f"Playlist: {playlist_title}\n\n"
                f"Download link: {result['downloadPage']}"
            )
            # Send log message for successful upload
            try:
                # Import only what's needed, not the app
                from main import send_log, active_processes, user_data
                
                # Pass the app instance to the function instead
                # Modify the function signature in main.py to accept app parameter
                log_message = (
                    "#PlaylistBotLogs \n"
                    f"✅ GoFile ZIP upload completed!\n"
                    f"👤 User: ID {user_id}\n"
                    f"🆔 ID: `{user_id}`\n"
                    f"📋 Playlist: {playlist_title}\n"
                    f"🔗 YouTube URL: {user_data.get(user_id, {}).get('url', 'Unknown URL')}\n"
                    f"📥 GoFile Link: {result['downloadPage']}"
                )
                await send_log(log_message)
                
                # Remove user from active processes
                active_processes.pop(user_id, None)
            except Exception as e:
                logger.error(f"Failed to send upload completion log: {str(e)}")
                
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