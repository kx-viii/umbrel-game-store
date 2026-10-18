#!/bin/bash
# Runs before the image's own entry script: passwords, the mod lists, and ZombieBuddy
DATA="${HOMEDIR}/Zomboid"
WORKSHOP="${STEAMAPPDIR}/steamapps/workshop"

# Passwords are made on the first start and kept in the app data, never in the repo
SECRETS="${DATA}/umbrel-secrets.env"
if [ ! -f "${SECRETS}" ]; then
  mkdir -p "${DATA}"
  {
    echo "JOIN_PASSWORD=$(tr -dc 'a-z0-9' </dev/urandom | head -c 10)"
    echo "ADMIN_PASSWORD=$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 20)"
  } > "${SECRETS}"
  chmod 600 "${SECRETS}"
fi
# The dashboard's RCON password, added on its own so older installs get one too
if ! grep -q '^RCON_PASSWORD=' "${SECRETS}"; then
  echo "RCON_PASSWORD=$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 24)" >> "${SECRETS}"
fi
. "${SECRETS}"
export PASSWORD="${JOIN_PASSWORD}"
export RCONPASSWORD="${RCON_PASSWORD}"
# Only needed to create the admin account, since the server logs its arguments
if [ ! -f "${DATA}/db/${SERVERNAME}.db" ]; then
  export ADMINPASSWORD="${ADMIN_PASSWORD}"
fi

# The mod lines are written here rather than by the entry script, whose sed escaping
# is fragile with the backslash B42 wants in front of each mod ID
read_list() { tr -d '\r' < "$1" | grep -v '^[[:space:]]*#' | sed '/^[[:space:]]*$/d'; }
INI="${DATA}/Server/${SERVERNAME}.ini"
mkdir -p "${DATA}/Server"
touch "${INI}"
set_line() {
  grep -v "^$1=" "${INI}" > "${INI}.tmp"
  printf '%s=%s\n' "$1" "$2" >> "${INI}.tmp"
  mv "${INI}.tmp" "${INI}"
}
set_line Mods "$(read_list /umbrel/mods.txt | awk '{print "\\" $0}' | paste -sd ';')"
set_line WorkshopItems "$(read_list /umbrel/workshop.txt | paste -sd ';')"
export SELF_MANAGED_MODS=true

# The dashboard's live map script runs from the server's own Lua folder. Players don't have
# that file, so the Lua checksum check has to be off or nobody could join.
cp -f /umbrel/umbrel-live.lua "${STEAMAPPDIR}/media/lua/server/UmbrelLive.lua"
set_line DoLuaChecksum false
set_line RCONPort 27015

# ZombieBuddy is a Java agent that several of the mods need on the server too.
# Fetch it before the server starts so the agent is there on the very first boot.
mkdir -p "${WORKSHOP}"
chown -R "${USER}:${USER}" "${WORKSHOP}"
ZB_JAR="${WORKSHOP}/content/108600/3619862853/mods/ZombieBuddy/libs/ZombieBuddy.jar"
runuser -u "${USER}" -- bash "${STEAMCMDDIR}/steamcmd.sh" +force_install_dir "${STEAMAPPDIR}" \
  +login anonymous +workshop_download_item 108600 3619862853 +quit \
  || echo "*** WARNING: SteamCMD couldn't update ZombieBuddy ***"

JSON="${STEAMAPPDIR}/ProjectZomboid64.json"
if [ -f "${ZB_JAR}" ]; then
  cp "${ZB_JAR}" "${STEAMAPPDIR}/ZombieBuddy.jar"
  # allow-all: there's no one to click the approval prompt on a server, and the mod list is fixed above
  AGENT="-javaagent:${STEAMAPPDIR}/ZombieBuddy.jar=policy=allow-all,frontend=console,config_dir=${DATA}/zombie_buddy"
  if ! grep -q -- "-javaagent" "${JSON}"; then
    sed -i "0,/\"vmArgs\" *: *\[/s||&\"${AGENT}\", |" "${JSON}"
  fi
  echo "*** INFO: ZombieBuddy agent enabled ***"
else
  echo "*** WARNING: ZombieBuddy isn't downloaded yet, so the server starts without it. Restart the app once the mods have downloaded ***"
fi

exec /server/scripts/entry.sh
