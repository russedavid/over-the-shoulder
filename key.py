from pynput import keyboard

def on_press(key):
    try:
        print(f"pressed vk value: {key.vk}")
    except AttributeError:
        print(f"vk value: {key.value.vk}")  # some special keys are wrapped differently

def on_release(key):
    try:
        print(f"released vk value: {key.vk}")
    except AttributeError:
        print(f"vk value (special): {key.value.vk}")  # some special keys are wrapped differently

    if key == keyboard.Key.esc:
        print("Exiting...")
        return False

with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
    listener.join()

