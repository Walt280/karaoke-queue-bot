import sqlalchemy as sa
import sqlalchemy.orm as sa_orm

Base = sa_orm.declarative_base()

class QueueEntry(Base):
    __tablename__ = "queue"

    id = sa.Column(sa.Integer, primary_key = True)
    guild_id = sa.Column(sa.BigInteger, nullable = False)
    user_id = sa.Column(sa.BigInteger, nullable = False)
    song_name = sa.Column(sa.String, nullable = True)
    queue_pos = sa.Column(sa.Integer, nullable = False)
    requeue = sa.Column(sa.Boolean, nullable = False)

    def __repr__(self) -> str:
        return f"QueueEntry: Guild={self.guild_id!r}, User={self.user_id!r}, Song={self.song_name!r}, QueuePos={self.queue_pos!r}"

class NextMsgEntry(Base):
    __tablename__ = "nextmsg"

    id = sa.Column(sa.Integer, primary_key = True)
    guild_id = sa.Column(sa.BigInteger, nullable = False)
    msg = sa.Column(sa.String, nullable = False)
    has_song = sa.Column(sa.Boolean, nullable = False)
    name = sa.Column(sa.String, nullable = False)

    def __repr__(self) -> str:
        return f"NextMsgEntry: Guild={self.guild_id!r}, Message={self.msg!r}, HasSongElem={self.has_song!r}"