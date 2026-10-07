
import os
import sys
import subprocess
import logging
from pathlib import Path
from PIL import Image

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/Users/niels/logs/setup_screensaver.log'),
        logging.StreamHandler() ])
logger = logging.getLogger(__name__)

class BinanceScreensaverSetup:
    def __init__(self):
        self.local_cache_dir = Path.home() / "Desktop" / "binance_charts"
        self.screensaver_dir = Path.home() / "Pictures" / "binance_screensaver"
        self.screensaver_dir.mkdir(exist_ok=True)
        
    def setup_folder_screensaver(self):
        """Setup screensaver to use a folder of images"""
        try:
            logger.info("Setting up folder-based screensaver...")
            
            # Clear existing screensaver images
            for file in self.screensaver_dir.glob("*.png"):
                file.unlink()
            
            # Copy chart images to screensaver directory
            chart_files = list(self.local_cache_dir.glob("*.png"))
            
            if not chart_files:
                logger.warning("No chart images found in cache directory")
                return False
            
            for i, chart_file in enumerate(chart_files):
                try:
                    # Copy and resize for screensaver
                    with Image.open(chart_file) as img:
                        # Resize to common screensaver resolution
                        img.thumbnail((1920, 1080), Image.Resampling.LANCZOS)
                        
                        # Save to screensaver directory
                        screensaver_path = self.screensaver_dir / f"binance_chart_{i:03d}.png"
                        img.save(screensaver_path)
                        logger.info(f"Added to screensaver: {screensaver_path}")
                        
                except Exception as e:
                    logger.error(f"Error processing {chart_file}: {e}")
            
            # Set up screensaver preferences
            self.configure_screensaver_preferences()
            
            logger.info("Folder screensaver setup completed")
            return True
            
        except Exception as e:
            logger.error(f"Error setting up folder screensaver: {e}")
            return False
    
    def configure_screensaver_preferences(self):
        """Configure macOS screensaver preferences"""
        try:
            logger.info("Configuring screensaver preferences...")
            
            # Set screensaver to use the folder
            subprocess.run([
                'defaults', '-currentHost', 'write', 'com.apple.screensaver',
                'moduleDict', '-dict',
                'path', '/System/Library/Screen Savers/Random.saver',
                'type', '0'
            ])
            
            # Set the folder path (this is a workaround since macOS doesn't directly support folder screensavers)
            # We'll use the Random screensaver and point it to our folder
            
            # Set screensaver delay to 5 minutes
            subprocess.run([
                'defaults', '-currentHost', 'write', 'com.apple.screensaver',
                'idleTime', '-int', '300'
            ])
            
            # Set screensaver to require password after 5 seconds
            subprocess.run([
                'defaults', '-currentHost', 'write', 'com.apple.screensaver',
                'askForPassword', '-bool', 'true'
            ])
            
            subprocess.run([
                'defaults', '-currentHost', 'write', 'com.apple.screensaver',
                'askForPasswordDelay', '-int', '5'
            ])
            
            logger.info("Screensaver preferences configured")
            
        except Exception as e:
            logger.error(f"Error configuring screensaver preferences: {e}")
    
    def create_animated_screensaver(self):
        """Create an animated screensaver that cycles through images"""
        try:
            logger.info("Creating animated screensaver...")
            
            # Create AppleScript to cycle through images
            applescript = f'''
tell application "System Events"
    set imageFolder to "{self.screensaver_dir}"
    set imageList to {{}}
    
    -- Get list of images
    set imageFiles to do shell script "ls " & quoted form of imageFolder & "/*.png"
    set AppleScript's text item delimiters to return
    set imageList to every text item of imageFiles
    set AppleScript's text item delimiters to ""
    
    repeat
        repeat with currentImage in imageList
            try
                set desktop picture to POSIX file currentImage
                delay 30 -- Show each image for 30 seconds
            end try
        end repeat
    end repeat
end tell
'''
            
            # Save AppleScript
            script_path = self.local_cache_dir / "binance_screensaver.applescript"
            with open(script_path, 'w') as f:
                f.write(applescript)
            
            logger.info(f"Animated screensaver script created: {script_path}")
            
            # Create a shell script to run the AppleScript
            shell_script = f'''#!/bin/bash
# Binance Charts Screensaver
cd "{self.local_cache_dir}"
osascript binance_screensaver.applescript
'''
            
            shell_path = self.local_cache_dir / "start_screensaver.sh"
            with open(shell_path, 'w') as f:
                f.write(shell_script)
            
            # Make executable
            os.chmod(shell_path, 0o755)
            
            logger.info(f"Screensaver launcher created: {shell_path}")
            
        except Exception as e:
            logger.error(f"Error creating animated screensaver: {e}")
    
    def setup_photo_screensaver(self):
        """Setup macOS Photo screensaver to use our folder"""
        try:
            logger.info("Setting up Photo screensaver...")
            
            # Try to set up the Photo screensaver to use our folder
            # This is more complex and may not work on all macOS versions
            
            # Set screensaver to Photo
            subprocess.run([
                'defaults', '-currentHost', 'write', 'com.apple.screensaver',
                'moduleDict', '-dict',
                'path', '/System/Library/Screen Savers/Photo\ Screen\ Saver.saver',
                'type', '0'
            ])
            
            # Try to set the photo source (this may not work on all versions)
            try:
                subprocess.run([
                    'defaults', '-currentHost', 'write', 'com.apple.screensaver',
                    'moduleDict', '-dict-add',
                    'path', '/System/Library/Screen Savers/Photo\ Screen\ Saver.saver'
                ])
            except:
                logger.warning("Could not set Photo screensaver source")
            
            logger.info("Photo screensaver setup attempted")
            
        except Exception as e:
            logger.error(f"Error setting up Photo screensaver: {e}")
    
    def create_desktop_widget(self):
        """Create a desktop widget that shows charts"""
        try:
            logger.info("Creating desktop widget...")
            
            # Create a simple HTML widget that can be opened in Dashboard or as a web page
            html_widget = f'''<!DOCTYPE html>
<html>
<head>
    <title>Binance Charts Widget</title>
    <style>
        body {{
            background: black;
            margin: 0;
            padding: 0;
            font-family: Arial, sans-serif;
        }}
        .chart-container {{
            position: relative;
            width: 100vw;
            height: 100vh;
            overflow: hidden;
        }}
        .chart-image {{
            width: 100%;
            height: 100%;
            object-fit: contain;
        }}
        .chart-info {{
            position: absolute;
            top: 10px;
            left: 10px;
            background: rgba(0,0,0,0.7);
            color: white;
            padding: 10px;
            border-radius: 5px;
            font-size: 12px;
        }}
    </style>
</head>
<body>
    <div class="chart-container">
        <div class="chart-info" id="chartInfo">Loading charts...</div>
        <img class="chart-image" id="chartImage" src="" alt="Binance Chart">
    </div>
    
    <script>
        const imageFolder = "{self.screensaver_dir}";
        let currentImageIndex = 0;
        let images = [];
        
        // This would need to be populated with actual image paths
        // For now, we'll use a placeholder
        images = [
            "{self.screensaver_dir}/binance_chart_000.png",
            "{self.screensaver_dir}/binance_chart_001.png",
            "{self.screensaver_dir}/binance_chart_002.png"
        ];
        
        function nextImage() {{
            if (images.length > 0) {{
                currentImageIndex = (currentImageIndex + 1) % images.length;
                document.getElementById('chartImage').src = images[currentImageIndex];
                document.getElementById('chartInfo').textContent = 
                    `Chart ${{currentImageIndex + 1}} of ${{images.length}}`;
            }}
        }}
        
        // Change image every 30 seconds
        setInterval(nextImage, 30000);
        
        // Load first image
        if (images.length > 0) {{
            nextImage();
        }}
    </script>
</body>
</html>
'''
            
            widget_path = self.local_cache_dir / "binance_charts_widget.html"
            with open(widget_path, 'w') as f:
                f.write(html_widget)
            
            logger.info(f"Desktop widget created: {widget_path}")
            
        except Exception as e:
            logger.error(f"Error creating desktop widget: {e}")
    
    def setup_complete_solution(self):
        """Setup the complete screensaver and desktop solution"""
        try:
            logger.info("Setting up complete screensaver solution...")
            
            # Setup folder screensaver
            self.setup_folder_screensaver()
            
            # Create animated screensaver
            self.create_animated_screensaver()
            
            # Try photo screensaver
            self.setup_photo_screensaver()
            
            # Create desktop widget
            self.create_desktop_widget()
            
            logger.info("Complete screensaver solution setup completed")
            
            # Print instructions
            print("\n" + "="*60)
            print("BINANCE CHARTS SCREENSAVER SETUP COMPLETED")
            print("="*60)
            print("\nTo activate the screensaver:")
            print("1. Go to System Preferences > Desktop & Screen Saver")
            print("2. Select 'Random' or 'Photo' screensaver")
            print("3. Set the delay to 5 minutes")
            print("\nAlternative: Run the animated screensaver manually:")
            print(f"   {self.local_cache_dir}/start_screensaver.sh")
            print("\nDesktop widget available at:")
            print(f"   {self.local_cache_dir}/binance_charts_widget.html")
            print("\nScreensaver images stored in:")
            print(f"   {self.screensaver_dir}")
            print("="*60)
            
        except Exception as e:
            logger.error(f"Error setting up complete solution: {e}")

def main():
    """Main function"""
    try:
        setup = BinanceScreensaverSetup()
        setup.setup_complete_solution()
        
    except Exception as e:
        logger.error(f"Setup error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()


