# Home Games

Community app store for umbrelOS with game servers.

To add it: Umbrel **App Store** > **⋯** (top right) > **Community App Stores**, paste this repo's URL and select **Add**.

## Core Keeper

A Core Keeper dedicated server ([escapingnetwork/core-keeper-dedicated](https://github.com/escapingnetwork/core-keeper-dedicated)).
Players join over Steam's relay with the Game ID, so no ports are opened on the router.

1. Install "Core Keeper" and open it. The first start downloads the server from Steam (about 600 MB).
2. When the page shows the Game ID, players join in Core Keeper with Play > Join game > Game ID.
3. The ID is kept in `ServerConfig.json` in the app data, so it stays the same across restarts.
   It's never stored in this repo; anyone with it can join.

The server updates from Steam on every start. Optional custom variables for the `server` service:
`WORLD_NAME`, `WORLD_MODE` (0 Normal, 1 Hard, 2 Creative, 4 Casual), `MAX_PLAYERS`, `WORLD_SEED`.
