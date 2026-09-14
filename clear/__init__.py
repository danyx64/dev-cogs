from .clear import ChannelClear


async def setup(bot):
    await bot.add_cog(ChannelClear(bot))
