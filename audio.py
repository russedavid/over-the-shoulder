import sys
import objc
from PyObjCTools import AppHelper
from Foundation import NSLog
import ScreenCaptureKit as SCK

def get_shareable_content(callback):
    Content = SCK.SCShareableContent
    if hasattr(Content, "getShareableContentWithCompletionHandler_"):
        Content.getShareableContentWithCompletionHandler_(callback)
    elif hasattr(Content, "currentShareableContentWithCompletionHandler_"):
        Content.currentShareableContentWithCompletionHandler_(callback)
    else:
        raise RuntimeError("SCShareableContent API name not found; update macOS/PyObjC.")

def list_apps():
    def on_content(content, error):
        if error is not None:
            NSLog(f"Error: {error}")
            AppHelper.stopEventLoop()
            return

        apps = list(content.applications())
        NSLog(f"Found {len(apps)} applications with shareable content:\n")
        for app in apps:
            NSLog(f"{app.applicationName()} — {app.bundleIdentifier()}")

        AppHelper.stopEventLoop()

    get_shareable_content(on_content)

if __name__ == "__main__":
    list_apps()
    AppHelper.runConsoleEventLoop()

