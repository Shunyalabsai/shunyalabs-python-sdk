__version__ = "1.1.7"

# Sent as "client" in the STT and TTS init messages (and as the TTS socket's
# User-Agent) so the services can log which plugin version a customer runs.
_CLIENT_ID = f"livekit-plugins-shunyalabsai/{__version__}"
