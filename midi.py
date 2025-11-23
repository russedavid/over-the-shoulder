import mido

# List available input ports (MIDI devices)
print("Available MIDI devices:")
for name in mido.get_input_names():
    print("  ", name)

# Open the first device (replace with your device name if needed)
with mido.open_input(mido.get_input_names()[0]) as inport:
    print("Listening for MIDI messages... (Ctrl+C to stop)")
    for msg in inport:
        print(msg)

