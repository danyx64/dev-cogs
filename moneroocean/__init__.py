from .moneroocean import MoneroOcean


async def setup(bot):
    await bot.add_cog(MoneroOcean(bot))
