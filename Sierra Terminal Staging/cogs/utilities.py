import discord
from discord.ext import commands

class SDInfo(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="sdinfo")
    async def sdinfo(self, ctx: commands.Context):
        SDembed = discord.Embed(
            title="Security Department Information",
            description="The Security Department is the primary combative force of the SCP Foundation. The department is responsible for the security of key areas, safety of personnel, and order of Class-D personnel.",
            color=discord.Color.dark_blue()
        )

        SDembed.add_field(
            name="💂 Department Subdivisions",
            value="Lorem Ipsum",
            inline=False
        )

        SDembed.add_field(
            name="📃 Department Documentation",
            value=f"- [Security Department Guidelines](https://docs.google.com/document/d/1HrtZkaoxKSi7mjZAnqvGzO_D9ADmuACEMxunep2RbEI/edit?tab=t.0#heading=h.74kaa0ooitq0)",
            inline=False
        )

        SDembed.set_footer(text=f"Requested by {ctx.author}")

        await ctx.send(embed=SDembed)

    @commands.command(name="scdinfo")
    async def scdinfo(self, ctx: commands.Context):
        SCDembed = discord.Embed(
            title="Scientific Department Information",
            description="The Scientific Department is responsible for the research and study of SCP objects to expand the Foundation's knowledge and understanding of them.",
            color=discord.Color.dark_blue()
        )

        SCDembed.add_field(
            name="💂 Department Subdivisions",
            value="Lorem Ipsum",
            inline=False
        )

        SCDembed.add_field(
            name="📃 Department Documentation",
            value=f"- Scientific Department Guidelines",
            inline=False
        )

        SCDembed.set_footer(text=f"Requested by {ctx.author}")

        await ctx.send(embed=SCDembed)

async def setup(bot: commands.Bot):
    await bot.add_cog(SDInfo(bot))
        