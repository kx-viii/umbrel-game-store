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

## Project Zomboid

A Build 42 dedicated server ([Danixu/project-zomboid-server-docker](https://github.com/Danixu/project-zomboid-server-docker))
running the [Project Viewpoint Vanilla+](https://steamcommunity.com/sharedfiles/filedetails/?id=3812346398) modpack.

1. Install "Project Zomboid" and open it. The first start downloads about 3 GB of mods and can take 20 minutes or more.
2. The page shows the status, the IP and port (16261), and the server and admin passwords.
   The passwords are made on the first start and kept in the app data, never in this repo.
3. Players need the same mods (subscribe to the collection and set up ZombieBuddy), then **Join** and add the server.

- `mods.txt` is the mod load order and `workshop.txt` the Workshop items; edit them and restart to change the mods.
- ZombieBuddy runs on the server as a Java agent with `policy=allow-all`, since no one can answer its approval prompt there.
- The image tag pins the game version, which must match the players' game. Bump it when Project Zomboid updates.
- Players outside the home network or Tailscale need UDP 16261–16262 forwarded on the router.
