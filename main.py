import logging
import os
import os.path
import sys
import yaml

import KaraokeQueueBot

from nextcord.ext import commands

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    script_path = os.path.realpath(__file__)
    config_path = os.path.join(os.path.dirname(script_path), "config.yaml")
    
    if(not os.path.exists(config_path)):
        logging.error(f"Config file not found! Please put it next to the bot python file (at {script_path}). You can find a sample config file in the repo.")
        sys.exit(-1)
    
    config = None
    with open(config_path, "r+", encoding="UTF-8") as config_file:
        config = yaml.load(config_file, Loader=yaml.Loader)
    
    bot_config = KaraokeQueueBot.KaraokeQueueBotConfig.from_yaml_data(config)
    bot = commands.Bot()
    queue_bot = KaraokeQueueBot.KaraokeQueueBot(bot, bot_config)
    bot.run(config["config"]["discord_token"])