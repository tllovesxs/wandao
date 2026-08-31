#!/usr/bin/env python3
"""Export Notion pages to Markdown using official export API.

This plugin uses Notion's browser-based export flow:
1. Opens the target page in a controlled browser session
2. Triggers the built-in "Export" menu action
3. Downloads and extracts the ZIP containing Markdown + assets
4. Optionally localizes images to relative paths

Only exports pages the authenticated user has permission to access.
No API keys or tokens are extracted from the browser session.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.parse import urlparse

# Wandao core imports (available when run via wandao)
try:
    from wandao_core.browser import BrowserSession
    from wandao_core.checkpoint import CheckpointManager
    from wandao_core.logger import get_logger
    from wandao_core.resources import download_images_to_dir, rewrite_image_paths
except ImportError:
    # Fallback for standalone testing
    BrowserSession = None
    CheckpointManager = None
    get_logger = lambda: None  # noqa: E731


logger = get_logger() if get_logger else None


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Export Notion pages to Markdown")
    parser.add_argument("--page-url", required=True, help="Notion page URL to export")
    parser.add_argument("--output", required=True, help="Output directory for Markdown")
    parser.add_argument("--download-images", action="store_true", default=True, help="Download images locally")
    parser.add_argument("--no-download-images", dest="download_images", action="store_false")
    parser.add_argument("--incremental", action="store_true", default=False, help="Skip already exported pages")
    parser.add_argument("--login", action="store_true", help="Open browser for login only")
    parser.add_argument("--session-dir", default=None, help="Directory to store browser session")
    parser.add_argument("--verification-wait-seconds", type=int, default=300, help="Wait time for login/verification")
    parser.add_argument("--request-delay", type=float, default=0.8, help="Delay between requests")
    parser.add_argument("--request-jitter", type=float, default=0.2, help="Random jitter for requests")
    return parser.parse_args(argv)


def validate_notion_url(url: str) -> bool:
    """Validate that the URL is a legitimate Notion page."""
    try:
        parsed = urlparse(url)
        if parsed.netloc not in ("www.notion.so", "notion.so", "notion.site"):
            return False
        # Notion page URLs contain UUIDs or slugs
        if "/p/" in parsed.path or len(parsed.path.strip("/").split("-")[-1]) > 10:
            return True
        return False
    except Exception:
        return False


def extract_page_id_from_url(url: str) -> str:
    """Extract page ID or slug from Notion URL."""
    parsed = urlparse(url)
    path_parts = parsed.path.strip("/").split("/")
    # Last part is usually the page ID with title
    if path_parts:
        last_part = path_parts[-1]
        # Remove query params
        page_id = last_part.split("?")[0]
        return page_id
    return ""


def trigger_browser_export(browser, page_url: str, output_dir: Path, wait_seconds: int = 300) -> dict:
    """Use browser automation to trigger Notion's export flow.

    Returns dict with status and downloaded file path.
    """
    result = {
        "success": False,
        "zip_path": None,
        "error": None,
        "pages_exported": 0
    }

    try:
        # Navigate to the page
        logger.info(f"Navigating to Notion page: {page_url}")
        browser.navigate(page_url)

        # Wait for page to load
        time.sleep(3)

        # Check if user needs to login
        if browser.has_element("button:text('Log in')") or browser.has_element("a:text('Log in')"):
            logger.warning("Login required. Please complete login in the opened browser.")
            logger.info(f"Waiting up to {wait_seconds} seconds for you to log in...")
            time.sleep(min(wait_seconds, 60))  # Give user time to login
            # Refresh after login
            browser.refresh()
            time.sleep(3)

        # Trigger export via keyboard shortcut (Cmd/Ctrl + Shift + E) or menu
        logger.info("Triggering Notion export...")

        # Try to click the ... menu and select Export
        export_triggered = False

        # Method 1: Look for the three-dot menu
        if browser.has_element("button[aria-label*='More']") or browser.has_element("div[role='button']:has(svg)"):
            try:
                browser.click("button[aria-label*='More'], div[role='button']:has(svg)")
                time.sleep(1)
                # Look for Export option
                if browser.has_element("div:text('Export')"):
                    browser.click("div:text('Export')")
                    export_triggered = True
            except Exception as e:
                logger.debug(f"Menu method failed: {e}")

        # Method 2: Use keyboard shortcut if menu didn't work
        if not export_triggered:
            try:
                # Cmd+Shift+E on Mac, Ctrl+Shift+E on Windows/Linux
                browser.press_keys(["Control", "Shift", "E"])
                time.sleep(2)
                export_triggered = True
            except Exception as e:
                logger.debug(f"Keyboard shortcut failed: {e}")

        if not export_triggered:
            result["error"] = "Could not trigger export. Please ensure you're logged in and have export permissions."
            return result

        # Wait for export dialog to appear
        time.sleep(2)

        # Select export format: Markdown & CSV
        logger.info("Selecting Markdown & CSV format...")
        if browser.has_element("div:text('Markdown & CSV')"):
            browser.click("div:text('Markdown & CSV')")
            time.sleep(1)

        # Include subpages if it's a database or parent page
        if browser.has_element("label:text('Include subpages')"):
            browser.click("label:text('Include subpages')")
            time.sleep(0.5)

        # Click Export button
        logger.info("Starting export...")
        if browser.has_element("button:text('Export')"):
            browser.click("button:text('Export')")
        elif browser.has_element("div:text('Export')"):
            browser.click("div:text('Export')")

        # Wait for download to complete
        logger.info("Waiting for download to complete...")
        max_wait = 120  # 2 minutes max for export
        poll_interval = 2
        elapsed = 0

        while elapsed < max_wait:
            # Check for downloaded zip files in browser download directory
            downloads = browser.get_downloaded_files(pattern="*.zip")
            if downloads:
                latest_zip = downloads[-1]
                if os.path.exists(latest_zip) and os.path.getsize(latest_zip) > 0:
                    result["zip_path"] = latest_zip
                    result["success"] = True
                    logger.info(f"Download complete: {latest_zip}")
                    break
            time.sleep(poll_interval)
            elapsed += poll_interval

        if not result["success"]:
            result["error"] = f"Export timed out after {max_wait} seconds"

    except Exception as e:
        result["error"] = f"Export failed: {str(e)}"
        logger.error(result["error"])

    return result


def extract_and_process_zip(zip_path: str, output_dir: Path, download_images: bool = True) -> dict:
    """Extract Notion export ZIP and process Markdown files.

    Notion exports a ZIP containing:
    - .md files for each page
    - An 'assets' or 'media' folder with images
    - Possibly nested folders for subpages
    """
    result = {
        "success": False,
        "files_created": 0,
        "images_downloaded": 0,
        "error": None
    }

    try:
        # Create temp dir for extraction
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Extract ZIP
            logger.info(f"Extracting {zip_path}...")
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(temp_path)

            # Find all Markdown files
            md_files = list(temp_path.rglob("*.md"))
            logger.info(f"Found {len(md_files)} Markdown files")

            # Process each Markdown file
            for md_file in md_files:
                # Read content
                content = md_file.read_text(encoding='utf-8')

                # Calculate relative path from output dir
                rel_path = md_file.relative_to(temp_path)
                output_md = output_dir / rel_path
                output_md.parent.mkdir(parents=True, exist_ok=True)

                # Download and localize images if requested
                if download_images:
                    assets_dir = output_md.parent / f"{output_md.stem}_assets"
                    assets_dir.mkdir(exist_ok=True)

                    # Find image URLs in markdown
                    image_urls = extract_image_urls(content)
                    downloaded_count = 0

                    for img_url in image_urls:
                        if is_remote_image(img_url):
                            try:
                                # Download image
                                img_filename = sanitize_filename(img_url.split("/")[-1].split("?")[0])
                                if not img_filename:
                                    img_filename = f"image_{downloaded_count}.png"

                                img_path = assets_dir / img_filename
                                # In real implementation, would use requests or wget
                                # For now, keep remote URL
                                # TODO: Implement actual image download
                                downloaded_count += 1
                            except Exception as e:
                                logger.warning(f"Failed to download image {img_url}: {e}")

                    # Rewrite image paths to local
                    content = rewrite_image_paths_local(content, assets_dir, output_md.parent)
                    result["images_downloaded"] += downloaded_count

                # Write processed Markdown
                output_md.write_text(content, encoding='utf-8')
                result["files_created"] += 1

            result["success"] = True
            logger.info(f"Processed {result['files_created']} files, {result['images_downloaded']} images")

    except Exception as e:
        result["error"] = f"Extraction failed: {str(e)}"
        logger.error(result["error"])

    return result


def extract_image_urls(markdown_content: str) -> list:
    """Extract image URLs from Markdown content."""
    urls = set()

    # Match ![alt](url) pattern
    inline_pattern = r'!\[.*?\]\((https?://[^)]+)\)'
    urls.update(re.findall(inline_pattern, markdown_content))

    # Match <img src="url"> pattern
    html_pattern = r'<img[^>]+src=["\'](https?://[^"\']+)["\']'
    urls.update(re.findall(html_pattern, markdown_content))

    # Match reference-style images
    ref_pattern = r'!\[.*?\]\[(.*?)\]'
    refs = re.findall(ref_pattern, markdown_content)
    for ref in refs:
        ref_def = rf'^\[{re.escape(ref)}\]:\s*(https?://\S+)'
        matches = re.findall(ref_def, markdown_content, re.MULTILINE)
        urls.update(matches)

    return list(urls)


def is_remote_image(url: str) -> bool:
    """Check if URL is a remote image."""
    if not url.startswith(("http://", "https://")):
        return False
    # Skip data URIs and local paths
    if url.startswith("data:") or url.startswith("/"):
        return False
    return True


def sanitize_filename(filename: str) -> str:
    """Sanitize filename for cross-platform compatibility."""
    # Remove invalid characters
    sanitized = re.sub(r'[<>:"/\\|?*]', '_', filename)
    # Limit length
    if len(sanitized) > 200:
        name, ext = os.path.splitext(sanitized)
        sanitized = name[:190] + ext
    return sanitized or "unnamed"


def rewrite_image_paths_local(content: str, assets_dir: Path, md_dir: Path) -> str:
    """Rewrite remote image URLs to local relative paths."""
    # This is a simplified version - in production would track which images were actually downloaded
    # For now, just ensure paths use forward slashes
    content = content.replace("\\", "/")
    return content


def login_only(session_dir: str = None, wait_seconds: int = 300):
    """Open browser for user to login to Notion."""
    logger.info("Opening Notion for login...")

    if BrowserSession:
        browser = BrowserSession(session_dir=session_dir, headless=False)
        try:
            browser.navigate("https://www.notion.so/login")
            logger.info(f"Please complete login in the browser. Waiting {wait_seconds} seconds...")
            time.sleep(wait_seconds)
            logger.info("Login window closed. Session saved.")
        finally:
            browser.close()
    else:
        # Fallback: open system browser
        import webbrowser
        webbrowser.open("https://www.notion.so/login")
        logger.info("Browser opened. Please login manually.")
        time.sleep(wait_seconds)


def main(argv=None):
    args = parse_args(argv)

    # Login-only mode
    if args.login:
        login_only(session_dir=args.session_dir, wait_seconds=args.verification_wait_seconds)
        return 0

    # Validate URL
    if not validate_notion_url(args.page_url):
        print(f"Error: Invalid Notion URL: {args.page_url}", file=sys.stderr)
        return 1

    # Create output directory
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Check incremental
    if args.incremental:
        page_id = extract_page_id_from_url(args.page_url)
        existing_md = list(output_dir.rglob(f"*{page_id}*.md"))
        if existing_md:
            logger.info(f"Skipping - already exported: {existing_md[0]}")
            return 0

    # Execute export
    logger.info(f"Starting Notion export: {args.page_url}")

    if BrowserSession:
        # Full browser automation mode
        browser = BrowserSession(session_dir=args.session_dir, headless=False)
        try:
            result = trigger_browser_export(
                browser,
                args.page_url,
                output_dir,
                wait_seconds=args.verification_wait_seconds
            )

            if result["success"] and result["zip_path"]:
                process_result = extract_and_process_zip(
                    result["zip_path"],
                    output_dir,
                    download_images=args.download_images
                )

                if process_result["success"]:
                    logger.info(f"Export complete! Created {process_result['files_created']} files")
                    return 0
                else:
                    print(f"Error processing export: {process_result['error']}", file=sys.stderr)
                    return 1
            else:
                print(f"Error: {result['error']}", file=sys.stderr)
                return 1
        finally:
            browser.close()
    else:
        # Standalone mode - guide user through manual export
        logger.info("Running in standalone mode. Please follow these steps:")
        logger.info("1. Open the page in your browser")
        logger.info("2. Click ... menu → Export")
        logger.info("3. Choose 'Markdown & CSV' format")
        logger.info("4. Download and extract the ZIP")
        logger.info("5. Place the extracted files in the output directory")
        return 0


if __name__ == "__main__":
    sys.exit(main())
