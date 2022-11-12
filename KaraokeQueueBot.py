import asyncio
import logging
import os
import os.path
import random
import string
import sqlalchemy as sa
import sqlalchemy.ext as sa_ext
import sqlalchemy.ext.asyncio as sa_async
import sqlalchemy.future as sa_future
import sqlalchemy.orm as sa_orm

import nextcord
from nextcord.ext import commands

from KaraokeQueueBotObjects import QueueEntry, NextMsgEntry, Base

class KaraokeQueueBotConfigError(Exception):
    pass

class KaraokeQueueBotConfig():
    def __init__(self, log_path: str, db_path: str, log_level: int, guild_ids: list):
        self.log_path = log_path
        self.db_path = db_path
        self.log_level = log_level
        self.guild_ids = guild_ids

    @classmethod
    def default(cls, base_dir: str, guild_ids = []):
        return cls(
            os.path.join(base_dir, "KaraokeQueueBot.log"),
            os.path.join(base_dir, "KaraokeQueueBot_data.db"),
            logging.INFO,
            guild_ids
        )

    @classmethod
    def from_yaml_data(cls, in_data: dict) -> None:
        log_str_map = {
            "critical": logging.CRITICAL,
            "error": logging.ERROR,
            "warning": logging.WARNING,
            "info": logging.INFO,
            "debug": logging.DEBUG
        }

        data = in_data["config"]
        
        log_path = data["log_path"]
        if(not log_path):
            raise KaraokeQueueBotConfigError("Log path is empty!")

        db_path = data["sqlite_database_path"]
        if(not db_path):
            raise KaraokeQueueBotConfigError("Database path is empty!")

        log_level = log_str_map.get(data["logging_level"].lower(), logging.INFO)

        guild_ids = data["guild_ids"]

        return cls(log_path, db_path, log_level, guild_ids)

class KaraokeQueueBot():
    def __init__(self, bot: commands.Bot, config: KaraokeQueueBotConfig) -> None:
        super().__init__()
        self.config = config
        self.db_engine = sa_async.create_async_engine(f"sqlite+aiosqlite:///{self.config.db_path}", future=True)
        self.db_sessionmaker = sa_orm.sessionmaker(bind=self.db_engine, expire_on_commit=False, class_=sa_async.AsyncSession, future=True)
        self.bot = bot

        async def create_tables(engine):
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        asyncio.run(create_tables(self.db_engine))

        logging.basicConfig(filename=self.config.log_path, encoding='UTF-8', level=self.config.log_level)
        self._register_commands()

    # Based on: https://stackoverflow.com/a/74012742
    def _register_commands(self) -> None:
        @self.bot.slash_command(guild_ids=self.config.guild_ids)
        async def queue(interaction: nextcord.Interaction) -> None:
            pass

        @queue.subcommand(name="list", description="Displays the current queue.")
        async def list_queue(
            interaction: nextcord.Interaction, 
            public: bool = nextcord.SlashOption(description="Display the queue publically.", required=False)
        ) -> None:
            async with self.db_sessionmaker() as session:
                queue_res = await self.get_queue(session, interaction.guild_id)
                queue_res = list(filter(lambda x: x.queue_pos != 0, queue_res))

                current_elem = await self.get_current(session, interaction.guild_id)
                current_elem_str = f"<@{current_elem.user_id}>" if current_elem != None else "nobody"
                queue_strs = [
                    f"Currently Up: {current_elem_str}\n",
                    "Current Queue:"
                ]
                
            if(not queue_res):
                queue_strs.append("Queue is empty!")
            else:
                for queue_elem in queue_res:
                    if(queue_elem.song_name is None):
                        queue_strs.append(f"{queue_elem.queue_pos}. <@{queue_elem.user_id}>")
                    else:
                        queue_strs.append(f"{queue_elem.queue_pos}. <@{queue_elem.user_id}> singing {queue_elem.song_name}")
            
            await interaction.send("\n".join(queue_strs), ephemeral=not public, allowed_mentions=nextcord.AllowedMentions(replied_user=True, everyone=False, users=[], roles=[]))

        @queue.subcommand(description="Add yourself to the end of the queue.")
        async def add(
            interaction: nextcord.Interaction, 
            song: str = nextcord.SlashOption(description="What song you'll sing.", required=False),
            requeue: bool = nextcord.SlashOption(description="Re-add you to the queue when your turn is over.", default=False, required=False)
        ) -> None:
            async with self.db_sessionmaker() as session, session.begin():
                in_queue = await self.check_in_queue(session, interaction.guild_id, interaction.user.id)
                if(in_queue):
                    await interaction.send("You are already in the queue!", ephemeral=True)
                    return

                await self.add_to_queue(session, interaction.guild_id, interaction.user.id, song, requeue)
                await session.commit()
            await interaction.send("You have been added to the queue.", ephemeral=True)

        @queue.subcommand(name="add-someone", description="Add a user to the end of the queue.")
        async def addsomeone(
            interaction: nextcord.Interaction, 
            user: nextcord.Member = nextcord.SlashOption(description="User to add to the queue.", required=True),
            song: str = nextcord.SlashOption(description="What song the enqueued will sing.", required=False),
            requeue: bool = nextcord.SlashOption(description="Re-add user to the queue when their turn is over.", default=False, required=False)
        ) -> None:
            async with self.db_sessionmaker() as session, session.begin():
                in_queue = await self.check_in_queue(session, interaction.guild_id, user.id)
                if(in_queue):
                    await interaction.send(f"<@{user.id}> is already in the queue!", ephemeral=True)
                    return

                await self.add_to_queue(session, interaction.guild_id, user.id, song, requeue)
                await session.commit()
            await interaction.send(f"Added <@{user.id}> to the queue.", ephemeral=True)

        @queue.subcommand(description="Remove yourself from the queue.")
        async def remove(interaction: nextcord.Interaction) -> None:
            async with self.db_sessionmaker() as session, session.begin():
                in_queue = await self.check_in_queue(session, interaction.guild_id, interaction.user.id)
                if(not in_queue):
                    await interaction.send("You are not in the queue!", ephemeral=True)
                    return

                await self.remove_from_queue(session, interaction.guild_id, interaction.user.id)
                await session.commit()
            await interaction.send("You have been removed from the queue.", ephemeral=True)

        @queue.subcommand(name="remove-someone", description="Remove a user from the queue.")
        async def removesomeone(
            interaction: nextcord.Interaction, 
            user: nextcord.Member = nextcord.SlashOption(description="User to add to the queue.", required=True)
        ) -> None:
            async with self.db_sessionmaker() as session, session.begin():
                in_queue = await self.check_in_queue(session, interaction.guild_id, user.id)
                if(not in_queue):
                    await interaction.send("<@{user.id}> is not in the queue!", ephemeral=True)
                    return

                await self.remove_from_queue(session, interaction.guild_id, user.id)
                await session.commit()
            await interaction.send(f"Removed <@{user.id}> from the queue.", ephemeral=True)

        @queue.subcommand(description="Move yourself to the bottom of the queue.")
        async def sink(interaction: nextcord.Interaction) -> None:
            async with self.db_sessionmaker() as session, session.begin():
                in_queue = await self.check_in_queue(session, interaction.guild_id, interaction.user.id)
                if(not in_queue):
                    await interaction.send("You are not in the queue!", ephemeral=True)
                    return

                length = await self.get_queue_length(session, interaction.guild_id)
                await self.move_queue_elem(session, interaction.guild_id, interaction.user.id, length)
                await session.commit()
            await interaction.send("You have been moved to the bottom of the queue.", ephemeral=True)

        @queue.subcommand(description="Swap the positions of two people in the queue.")
        async def swap(
            interaction: nextcord.Interaction,
            user1: nextcord.Member = nextcord.SlashOption(description="First user.", required=True), 
            user2: nextcord.Member = nextcord.SlashOption(description="Second user.", required=True)
        ) -> None:
            async with self.db_sessionmaker() as session, session.begin():
                u1_in_queue = await self.check_in_queue(session, interaction.guild_id, user1.id)
                u2_in_queue = await self.check_in_queue(session, interaction.guild_id, user2.id)
                if(not u1_in_queue):
                    await interaction.send("<@{user1.id}> is not in the queue!", ephemeral=True)
                    return
                if(not u2_in_queue):
                    await interaction.send("<@{user2.id}> is not in the queue!", ephemeral=True)
                    return
            
                u1_queue_elem = await self.get_queue_elem(session, interaction.guild_id, user1.id)
                u2_queue_elem = await self.get_queue_elem(session, interaction.guild_id, user2.id)
                u1_pos = u1_queue_elem.queue_pos
                u2_pos = u2_queue_elem.queue_pos
                await self.move_queue_elem(session, interaction.guild_id, user1.id, u2_pos)
                await self.move_queue_elem(session, interaction.guild_id, user2.id, u1_pos)
                await session.commit()
            await interaction.send(f"Swapped the positions of <@{user1.id}> and <@{user2.id}>.", ephemeral=True)
            
        @queue.subcommand(description="Clear the queue.")
        async def clear(interaction: nextcord.Interaction) -> None:
            async with self.db_sessionmaker() as session, session.begin():
                elems = await self.get_queue(session, interaction.guild_id)
                
                for elem in elems:
                    await session.delete(elem)

                await session.commit()
            await interaction.send("Queue cleared.", ephemeral=True)

        @queue.subcommand(name="edit-song", description="Edit your proposed song in the queue.")
        async def editsong(
            interaction: nextcord.Interaction,
            song: str = nextcord.SlashOption(description="What song you'll sing.", required=True)
        ) -> None:
            async with self.db_sessionmaker() as session, session.begin():
                in_queue = await self.check_in_queue(session, interaction.guild_id, interaction.user.id)
                if(not in_queue):
                    await interaction.send("You are not in the queue!", ephemeral=True)
                    return

                elem = await self.get_queue_elem(session, interaction.guild_id, interaction.user.id)
                elem.song_name = song
                await session.commit()

            await interaction.send(f"Song updated to \"{song}\".", ephemeral=True)

        @queue.subcommand(description="Move this user to a specific spot in the queue.")
        async def move(
            interaction: nextcord.Interaction,
            user: nextcord.Member = nextcord.SlashOption(description="User to move.", required=True),
            position: int = nextcord.SlashOption(description="Position to move user to.", required=True)
        ) -> None:
            async with self.db_sessionmaker() as session, session.begin():
                in_queue = await self.check_in_queue(session, interaction.guild_id, user.id)
                if(not in_queue):
                    await interaction.send("<@{user.id}> is not in the queue!", ephemeral=True)
                    return
                
                queue_len = await self.get_queue_length(session, interaction.guild_id)
                if(position <= 0 or position > queue_len):
                    await interaction.send(f"{position} is not a valid queue position.", ephemeral=True)
                    return
                
                await self.move_queue_elem(session, interaction.guild_id, user.id, position)
                await session.commit()
            await interaction.send(f"Moved <@{user.id}> to {position}.", ephemeral=True)

        @self.bot.slash_command(description="Advance the queue.", guild_ids=self.config.guild_ids)
        async def next(interaction: nextcord.Interaction):
            #<@{current_elem.user_id}> is up next! They'll be singing \"{current_elem.song_name}\"!
            DEFAULT_WITH_SONG = "{user} is up next! They'll be singing \"{song}\"!"
            DEFAULT_NO_SONG = "{user} is up next!"

            async with self.db_sessionmaker() as session, session.begin():
                current_elem = await self.get_current(session, interaction.guild_id)
                if(current_elem != None and not current_elem.requeue):
                    await session.delete(current_elem)
                elif(current_elem != None and current_elem.requeue):
                    queue_len = await self.get_queue_length(session, interaction.guild_id)
                    current_elem.queue_pos = queue_len + 1

                queue_len = await self.get_queue_length(session, interaction.guild_id)
                if(queue_len == 0):
                    await interaction.send("No one left in the queue!")
                    return

                await self.decrement_queue_numbers(session, interaction.guild_id, 1)

                current_elem = await self.get_current(session, interaction.guild_id)
                await session.commit()

            async with self.db_sessionmaker() as session:
                current_elem = await self.get_current(session, interaction.guild_id)
                stmt = sa.select(NextMsgEntry) \
                    .where(NextMsgEntry.guild_id == interaction.guild_id) \
                    .where(NextMsgEntry.has_song == bool(current_elem.song_name))
                stmt_res = await session.execute(stmt)
                nextmsgs = stmt_res.scalars().all()
                if(not nextmsgs and not bool(current_elem.song_name)):
                    template = DEFAULT_NO_SONG
                elif(not nextmsgs and bool(current_elem.song_name)):
                    template = DEFAULT_WITH_SONG
                else:
                    template = random.choice(nextmsgs).msg

                template_args = {
                    "user": f"<@{current_elem.user_id}>",
                    "song": current_elem.song_name
                }

            await interaction.send(template.format(**template_args))

        @self.bot.slash_command(description="See who's currently up.", guild_ids=self.config.guild_ids)
        async def current(interaction: nextcord.Interaction):
            async with self.db_sessionmaker() as session:
                current_elem = await self.get_current(session, interaction.guild_id)
            if(current_elem == None):
                await interaction.send("No one is up!")
            else:
                await interaction.send(f"<@{current_elem.user_id}> is currently up!", allowed_mentions=nextcord.AllowedMentions(replied_user=True, everyone=False, users=[], roles=[]))
                
        @self.bot.slash_command(guild_ids=self.config.guild_ids)
        async def nextmsg(interaction: nextcord.Interaction) -> None:
            pass

        @nextmsg.subcommand(name="list", description="Displays all the templates for the 'next up' messages.")
        async def nextmsglist(interaction: nextcord.Interaction) -> None:
            async with self.db_sessionmaker() as session:
                stmt = sa.select(NextMsgEntry).where(NextMsgEntry.guild_id == interaction.guild_id)
                stmt_res = await session.execute(stmt)
                nextmsgs = stmt_res.scalars().all()
            
            if(not nextmsgs):
                await interaction.send("No custom 'up next' messages defined!", ephemeral=True)
                return
            
            nextmsg_list = ["'Up Next' Custom Messages"] + [f"{i.name}: \"{i.msg}\"" for i in nextmsgs]

            await interaction.send("\n".join(nextmsg_list), ephemeral=True)
        
        @nextmsg.subcommand(name="add", description="Add a new 'next up' message template.")
        async def nextmsgadd(
            interaction: nextcord.Interaction,
            template: str = nextcord.SlashOption(description="The template message. Placeholders: {user} = username; {song} = song name.", required=True),
            name: str = nextcord.SlashOption(description="The name of this template message.", required=False)
        ) -> None:
            async with self.db_sessionmaker() as session, session.begin():
                name_in_db = True
                while(name_in_db):
                    name = name if name else "".join(random.choices(string.ascii_lowercase + string.digits, k = 8))
                    stmt = sa.select(sa.func.count(NextMsgEntry.id)).where(NextMsgEntry.name == name)
                    stmt_res = await session.execute(stmt)
                    name_in_db = stmt_res.scalar_one() > 0

                session.add(NextMsgEntry(
                    guild_id=interaction.guild_id,
                    msg=template,
                    has_song="{song}" in template,
                    name=name
                ))

                await session.commit()

            await interaction.send(f"Added template with name \"{name}\".", ephemeral=True)

        @nextmsg.subcommand(name="remove", description="Removes a 'next up' message template.")
        async def nextmsgremove(
            interaction: nextcord.Interaction,
            name: str = nextcord.SlashOption(description="The name of the template message to remove.", required=False)
        ) -> None:
            async with self.db_sessionmaker() as session, session.begin():
                stmt = sa.select(NextMsgEntry) \
                        .where(NextMsgEntry.guild_id == interaction.guild_id) \
                        .where(NextMsgEntry.name == name)
                stmt_res = await session.execute(stmt)
                nextmsg = stmt_res.scalar_one_or_none()
            
            if(not nextmsg):
                await interaction.send(f"Could not find 'up next' message with name \"{nextmsg.name}\"!")
                return
            
            await session.delete(nextmsg)
            await session.commit()
            
            await interaction.send(f"Removed template with name \"{name}\".", ephemeral=True)

    async def get_queue(self, session: sa_async.AsyncSession, guild_id: int) -> list:
        stmt = sa_future.select(QueueEntry).where(QueueEntry.guild_id == guild_id).order_by(QueueEntry.queue_pos)
        result = await session.execute(stmt)
        return result.scalars().all()

    async def get_queue_length(self, session: sa_async.AsyncSession, guild_id: int) -> int:
        stmt = sa_future.select(sa.func.count(QueueEntry.id)) \
            .where(QueueEntry.guild_id == guild_id) \
            .where(QueueEntry.queue_pos != 0)
        result = await session.execute(stmt)
        return result.scalar_one()

    async def get_queue_elem(self, session: sa_async.AsyncSession, guild_id: int, user_id: int) -> QueueEntry:
        stmt = sa_future.select(QueueEntry) \
            .where(QueueEntry.guild_id == guild_id) \
            .where(QueueEntry.user_id == user_id)
        result = await session.execute(stmt)
        return result.scalar_one()

    async def check_in_queue(self, session: sa_async.AsyncSession, guild_id: int, user_id: int) -> bool:
        stmt = sa_future.select(sa.func.count(QueueEntry.id)) \
            .where(QueueEntry.guild_id == guild_id) \
            .where(QueueEntry.user_id == user_id)
        result = await session.execute(stmt)
        return result.scalar_one() > 0

    async def get_current(self, session: sa_async.AsyncSession, guild_id: int) -> QueueEntry:
        stmt = sa_future.select(QueueEntry) \
            .where(QueueEntry.guild_id == guild_id) \
            .where(QueueEntry.queue_pos == 0)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def decrement_queue_numbers(self, session: sa_async.AsyncSession, guild_id: int, queue_num: int, queue_num_end = -1) -> None:
        stmt = sa_future.select(QueueEntry) \
            .where(QueueEntry.guild_id == guild_id) \
            .where(QueueEntry.queue_pos >= queue_num)

        if(queue_num_end != -1):
            stmt = stmt.where(QueueEntry.queue_pos <= queue_num_end)

        result = await session.execute(stmt)
        queue_elems = result.scalars().all()

        for elem in queue_elems:
            elem.queue_pos -= 1

    async def increment_queue_numbers(self, session: sa_async.AsyncSession, guild_id: int, queue_num: int, queue_num_end = -1) -> None:
        stmt = sa_future.select(QueueEntry) \
            .where(QueueEntry.guild_id == guild_id) \
            .where(QueueEntry.queue_pos >= queue_num)

        if(queue_num_end != -1):
            stmt = stmt.where(QueueEntry.queue_pos <= queue_num_end)
        
        result = await session.execute(stmt)
        queue_elems = result.scalars().all()

        for elem in queue_elems:
            elem.queue_pos += 1
    
    async def add_to_queue(self, session: sa_async.AsyncSession, guild_id: int, user_id: int, song: str = None, requeue = False) -> None:
        queue_pos = await self.get_queue_length(session, guild_id)
        queue_pos += 1
        session.add(
            QueueEntry(
                guild_id=guild_id,
                user_id=user_id,
                song_name=song,
                queue_pos=queue_pos,
                requeue=requeue
            )
        )

    async def remove_from_queue(self, session: sa_async.AsyncSession, guild_id: int, user_id: int) -> None:
        stmt = sa_future.select(QueueEntry) \
            .where(QueueEntry.guild_id == guild_id) \
            .where(QueueEntry.user_id == user_id)
        result = await session.execute(stmt)
        elem = result.scalar_one()
        
        await session.delete(elem)

        await self.decrement_queue_numbers(session, guild_id, elem.queue_pos)

    async def move_queue_elem(self, session: sa_async.AsyncSession, guild_id: int, user_id: int, new_queue_pos: int) -> None:
        elem = await self.get_queue_elem(session, guild_id, user_id)

        if(elem.queue_pos == new_queue_pos):
            return
        elif(elem.queue_pos > new_queue_pos):
            await self.increment_queue_numbers(session, guild_id, new_queue_pos, elem.queue_pos)
        elif(elem.queue_pos < new_queue_pos):
            await self.decrement_queue_numbers(session, guild_id, elem.queue_pos, new_queue_pos)

        elem.queue_pos = new_queue_pos