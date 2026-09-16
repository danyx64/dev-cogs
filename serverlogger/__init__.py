from .serverlogger_v5 import ServerLogger


async def setup(bot):
    await bot.add_cog(ServerLogger(bot))
