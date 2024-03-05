import io
import toml
import json
import asyncio
import gzip
import traceback
import yaml
import discord
from discord import Intents, Embed, ButtonStyle, Message, Attachment, File, RawReactionActionEvent, ApplicationContext, Color
from discord.ext import commands
from discord.ui import View, button
from PIL import Image
from collections import OrderedDict

CONFIG = toml.load('config.toml')
monitored = CONFIG.get('MONITORED_CHANNEL_IDS', [])
SCAN_LIMIT_BYTES = CONFIG.get('SCAN_LIMIT_BYTES', 40 * 1024**2)  # Default 40 MB
intents = Intents.default() | Intents.message_content | Intents.members
client = commands.Bot(intents=intents)

def comfyui_get_data(dat):
    try:
        aa = []
        dat = json.loads(dat)
        for key, value in dat.items():
            if value['class_type'] == "CLIPTextEncode":
                aa.append({"val": value['inputs']['text'],
                        "type": "prompt"})
            elif value['class_type'] == "CheckpointLoaderSimple":
                aa.append({"val": value['inputs']['ckpt_name'],
                        "type": "model"})
            elif value['class_type'] == "LoraLoader":
                aa.append({"val": value['inputs']['lora_name'],
                        "type": "lora"})
        return aa
    except Exception as e:
        print(e)
        return []

def get_params_from_string(param_str):
    output_dict = {}
    parts = param_str.split('Steps: ')
    prompts = parts[0]
    params = 'Steps: ' + parts[1]
    if 'Negative prompt: ' in prompts:
        output_dict['Prompt'] = prompts.split('Negative prompt: ')[0]
        output_dict['Negative Prompt'] = prompts.split('Negative prompt: ')[1]
        if len(output_dict['Negative Prompt']) > 1024:
            output_dict['Negative Prompt'] = output_dict['Negative Prompt'][:1020] + '...'
    else:
        output_dict['Prompt'] = prompts
    if len(output_dict['Prompt']) > 1024:
        output_dict['Prompt'] = output_dict['Prompt'][:1020] + '...'
    params = params.split(', ')
    for param in params:
        try:
            key, value = param.split(': ')
            output_dict[key] = value
        except ValueError:
            pass
    return output_dict


def get_embed(embed_dict, context: Message):
    embed = Embed(color=discord.Color.from_rgb(*CONFIG.get("EMBED_COLOR")))
    i = 0
    for key, value in embed_dict.items():
        if key.strip() == "" or value.strip() == "":
            continue
        i += 1
        if i >= 25:
            continue
        embed.add_field(name=key[:255], value=value[:1023], inline='Prompt' not in key)
    embed.set_footer(text=f'Posted by {context.author}', icon_url=context.author.display_avatar)
    return embed

def get_embed_nai(embed_dict_orig, context: Message):
    try:
        embed_dict = json.loads(embed_dict_orig["Comment"])
        embed = Embed(color=discord.Color.from_rgb(*CONFIG.get("EMBED_COLOR")))
        embed.add_field(name="Prompt", value=embed_dict['prompt'], inline=False)
        embed.add_field(name="Negative Prompt", value=embed_dict['uc'], inline=False)
        blacklist = ['prompt', 'uc', 'signed_hash', 'request_type']

        embed_dict['Model Hash'] = embed_dict_orig['Source'].split()[-1:][0].lower()
        for key, value in embed_dict.items():
            if value == None or value == 'None' or key in blacklist:
                continue
            embed.add_field(name=key, value=value, inline=True)
            if len(embed.fields) == 24:
                embed.add_field(name='Model Hash', value=embed_dict['Model Hash'], inline=True)
                break
        embed.set_footer(text=f'Posted by {context.author}', icon_url=context.author.display_avatar)
        return embed
    except Exception as error:
        print(traceback.format_exc())
def read_info_from_image_stealth(image: Image.Image):
    # trying to read stealth pnginfo
    width, height = image.size
    pixels = image.load()

    has_alpha = True if image.mode == "RGBA" else False
    mode = None
    compressed = False
    binary_data = ""
    buffer_a = ""
    buffer_rgb = ""
    index_a = 0
    index_rgb = 0
    sig_confirmed = False
    confirming_signature = True
    reading_param_len = False
    reading_param = False
    read_end = False
    for x in range(width):
        for y in range(height):
            if has_alpha:
                r, g, b, a = pixels[x, y]
                buffer_a += str(a & 1)
                index_a += 1
            else:
                r, g, b = pixels[x, y]
            buffer_rgb += str(r & 1)
            buffer_rgb += str(g & 1)
            buffer_rgb += str(b & 1)
            index_rgb += 3
            if confirming_signature:
                if index_a == len("stealth_pnginfo") * 8:
                    decoded_sig = bytearray(
                        int(buffer_a[i : i + 8], 2) for i in range(0, len(buffer_a), 8)
                    ).decode("utf-8", errors="ignore")
                    if decoded_sig in {"stealth_pnginfo", "stealth_pngcomp"}:
                        confirming_signature = False
                        sig_confirmed = True
                        reading_param_len = True
                        mode = "alpha"
                        if decoded_sig == "stealth_pngcomp":
                            compressed = True
                        buffer_a = ""
                        index_a = 0
                    else:
                        read_end = True
                        break
                elif index_rgb == len("stealth_pnginfo") * 8:
                    decoded_sig = bytearray(
                        int(buffer_rgb[i : i + 8], 2) for i in range(0, len(buffer_rgb), 8)
                    ).decode("utf-8", errors="ignore")
                    if decoded_sig in {"stealth_rgbinfo", "stealth_rgbcomp"}:
                        confirming_signature = False
                        sig_confirmed = True
                        reading_param_len = True
                        mode = "rgb"
                        if decoded_sig == "stealth_rgbcomp":
                            compressed = True
                        buffer_rgb = ""
                        index_rgb = 0
            elif reading_param_len:
                if mode == "alpha":
                    if index_a == 32:
                        param_len = int(buffer_a, 2)
                        reading_param_len = False
                        reading_param = True
                        buffer_a = ""
                        index_a = 0
                else:
                    if index_rgb == 33:
                        pop = buffer_rgb[-1]
                        buffer_rgb = buffer_rgb[:-1]
                        param_len = int(buffer_rgb, 2)
                        reading_param_len = False
                        reading_param = True
                        buffer_rgb = pop
                        index_rgb = 1
            elif reading_param:
                if mode == "alpha":
                    if index_a == param_len:
                        binary_data = buffer_a
                        read_end = True
                        break
                else:
                    if index_rgb >= param_len:
                        diff = param_len - index_rgb
                        if diff < 0:
                            buffer_rgb = buffer_rgb[:diff]
                        binary_data = buffer_rgb
                        read_end = True
                        break
            else:
                # impossible
                read_end = True
                break
        if read_end:
            break
    if sig_confirmed and binary_data != "":
        # Convert binary string to UTF-8 encoded text
        byte_data = bytearray(int(binary_data[i : i + 8], 2) for i in range(0, len(binary_data), 8))
        try:
            if compressed:
                decoded_data = gzip.decompress(bytes(byte_data)).decode("utf-8")
            else:
                decoded_data = byte_data.decode("utf-8", errors="ignore")
            return decoded_data
        except Exception as e:
            print(e)
            pass
    return None


@client.slash_command()
async def privacy(ctx):
    """
    Returns our privacy policy.
    """
    base = Embed(title="Privacy Policy", color=discord.Color.from_rgb(*CONFIG.get("EMBED_COLOR")))
    base.add_field(name="What we collect", value="Other than simple data from your user (mainly username, role color) not much else other than when an image is sent in a **monitored channel**, the bot downloads it to its RAM and processes it.\n***We do not store any of your data/images.***", inline=False)
    base.add_field(name="What we use/store", value="Whenever the bot has an error decoding an image, it will print out the error and data to the console. The data consists of the raw bytes in the image metadata. Whenever a mod/admin toggles a channel on/off, the bot will save the ID to storage in case of it crashing. Other than that, that is all we use/store.", inline=False)
    base.add_field(name="What we share", value="***We do not share any of your data/images.*** There's no use for them lol.", inline=False)
    base.add_field(name="Open Source? Where?!", value="Yes, its [here](https://github.com/itsolegdm/PI-Chan). We are licensed under the [MIT License](https://github.com/itsolegdm/PI-Chan/blob/main/LICENSE). \nThe code is based off salt's base and yoinked's's fork. ", inline=False)
    base.set_footer(text=f"Maintained by @itsolegdm, this channel is {'not' if not ctx.channel_id in monitored else ''} monitored", icon_url=ctx.author.display_avatar)
    # base.set_image(url="https://cdn.discordapp.com/avatars/1159983729591210004/8666dba0c893163fcf0e01629e85f6e8?size=1024")
    await ctx.respond(embed=base, ephemeral=True)
# @client.slash_command()
# async def toggle_channel(ctx: ApplicationContext, channel_id):
#     """
#     Adds/Removes a channel to the list of monitored channels for this bot.
#     channel_id: The ID of the channel to add. (defaults to current channel)
#
#     Permissions:
#     - Manage Messages
#     """
#     #perms
#     if not ctx.author.guild_permissions.manage_messages:
#         await ctx.respond("You do not have permission to use this command.", ephemeral=True)
#         return
#     try:
#         if channel_id:
#             channel_id = int(channel_id)
#         else:
#             channel_id = ctx.channel_id
#         if channel_id in monitored:
#             monitored.remove(channel_id)
#             await ctx.respond(f"Removed {channel_id} from the list of monitored channels.", ephemeral=True)
#         else:
#             monitored.append(channel_id)
#             await ctx.respond(f"Added {channel_id} to the list of monitored channels.", ephemeral=True)
#         #update the config
#         cfg = toml.load('config.toml')
#         cfg['MONITORED_CHANNEL_IDS'] = monitored
#         toml.dump(cfg, open('config.toml', 'w'))
#     except ValueError:
#         await ctx.respond("Invalid channel ID.", ephemeral=True)
#         return
#     except Exception as e:
#         print(f"{type(e).__name__}: {e}")
#         await ctx.respond(f"Internal bot error, please DM yoinked.", ephemeral=True)
        


@client.event
async def on_ready():
    print(f"Logged in as {client.user}!")


@client.event
async def on_message(message: Message):
    if message.channel.id in monitored and message.attachments:
        attachments = [a for a in message.attachments if a.filename.lower().endswith(".png") and a.size < SCAN_LIMIT_BYTES]
        for i, attachment in enumerate(attachments): # download one at a time as usually the first image is already ai-generated
            metadata = OrderedDict()
            await read_attachment_metadata(i, attachment, metadata)
            if metadata:
                # await message.add_reaction('🔎')
                emoji_shit = await message.guild.fetch_emoji(CONFIG.get("EMOJI_ID"))
                await message.add_reaction(emoji_shit)
                return


class MyView(View):
    def __init__(self):
        super().__init__(timeout=3600, disable_on_timeout=True)
        self.metadata = None

    @button(label='Full Parameters', style=ButtonStyle.green)
    async def details(self, button, interaction):
        button.disabled = True
        await interaction.response.edit_message(view=self)
        if len(self.metadata) > 1980:
            with io.StringIO() as f:
                indented = json.dumps(json.loads(self.metadata), sort_keys=True, indent=2)
                f.write(indented)
                f.seek(0)
                await interaction.followup.send(file=File(f, "parameters.yaml"))
        else:
            await interaction.followup.send(f"```yaml\n{self.metadata}```")


async def read_attachment_metadata(i: int, attachment: Attachment, metadata: OrderedDict):
    """Allows downloading in bulk"""
    try:
        image_data = await attachment.read()
        with Image.open(io.BytesIO(image_data)) as img:
            # try:
            #     info = img.info['parameters']
            # except:
            #     info = read_info_from_image_stealth(img)
            info = None
            if img.info:
                if 'parameters' in img.info:
                    info = img.info['parameters']
                elif 'prompt' in img.info:
                    info = img.info['prompt']
                elif img.info["Software"] == "NovelAI":
                    info = img.info
                    # info["Software"] = img.info["Software"]
                    # info["Source"] = img.info["Source"]
            else:
                info = read_info_from_image_stealth(img)

            if info:
                metadata[i] = info
    except:
        print(traceback.format_exc())


@client.event
async def on_raw_reaction_add(ctx: RawReactionActionEvent):
    """Send image metadata in reacted post to user DMs"""
    if ctx.emoji.id != CONFIG.get("EMOJI_ID") or ctx.channel_id not in monitored or ctx.member.bot:
        return
    channel = client.get_channel(ctx.channel_id)
    message = await channel.fetch_message(ctx.message_id)
    if not message:
        return
    attachments = [a for a in message.attachments if a.filename.lower().endswith(".png")]
    if not attachments:
        return
    # if ctx.emoji.name == '❔':
        # user_dm = await client.get_user(ctx.user_id).create_dm()
        # await user_dm.send(embed=Embed(title="Predicted Prompt", color=discord.Color.from_rgb(*CONFIG.get("EMBED_COLOR"))), description=GRADCL.predict(attachments[0].url, "chen-moat2", 0.4, True, True, api_name="/classify")[1]).set_image(url=attachments[0].url))
        # return
    metadata = OrderedDict()
    tasks = [read_attachment_metadata(i, attachment, metadata) for i, attachment in enumerate(attachments)]
    await asyncio.gather(*tasks) #this code is amazing. -yoinked; yes, it is - itsolegdm
    if not metadata:
        return
    user_dm = await client.get_user(ctx.user_id).create_dm()
    for attachment, data in [(attachments[i], data) for i, data in metadata.items()]:
        try:
            if 'Steps:' in data:
                try:
                    params = get_params_from_string(data)
                    embed = get_embed(params, message)
                    embed.set_image(url=attachment.url)
                    custom_view = MyView()
                    custom_view.metadata = data
                    await user_dm.send(view=custom_view, embed=embed, mention_author=False)
                except:
                    print(traceback.format_exc())
                    txt = "uh oh! PI-chan did a fucky wucky and cant pawse it into a neat view, so hewes the raw content\n >w<"
                    await user_dm.send(txt)
                    with io.StringIO() as f:
                        f.write(data)
                        f.seek(0)
                        await user_dm.send(file=File(f, "parameters.yaml"))
            elif 'Software' in data:
                if isinstance(data, str):
                    data = json.loads(data)
                embed = get_embed_nai(data, message)
                embed.set_image(url=attachment.url)
                custom_view = MyView()
                custom_view.metadata = data
                await user_dm.send(view=custom_view, embed=embed, mention_author=False)
            else:
                if "\"inputs\"" not in data:
                    continue
                
                i = 0
                embed = Embed(title="ComfyUI Parameters", color=discord.Color.from_rgb(*CONFIG.get("EMBED_COLOR")))
                for enum, dax in enumerate(comfyui_get_data(data)):
                    i += 1
                    if i >= 25:
                        break # why the hell continue? just break...
                    embed.add_field(name=f"{dax['type']} {enum+1} (beta)", value=dax['val'], inline=True)
                embed.set_footer(text=f'Posted by {message.author}', icon_url=message.author.display_avatar)
                embed.set_image(url=attachment.url)
                await user_dm.send(embed=embed, mention_author=False)
                with io.StringIO() as f:
                    indented = json.dumps(json.loads(data), sort_keys=True, indent=2)
                    f.write(indented)
                    f.seek(0)
                    await user_dm.send(file=File(f, "parameters.json"))
        
        except:
            print(data)
            print(traceback.format_exc())
            pass


@client.message_command(name="View Raw Prompt")
async def message_command(ctx: ApplicationContext, message: Message):
    """Get raw list of parameters for every image in this post."""
    attachments = [a for a in message.attachments if a.filename.lower().endswith(".png")]
    if not attachments:
        await ctx.respond("This post contains no matching images.", ephemeral=True)
        return
    await ctx.defer(ephemeral=True)
    metadata = OrderedDict()
    tasks = [read_attachment_metadata(i, attachment, metadata) for i, attachment in enumerate(attachments)]
    await asyncio.gather(*tasks)
    if not metadata:
        await ctx.respond(f"This post contains no image generation data.", ephemeral=True)
        return
    print(metadata.values())
    response = "\n\n".join(str(value) for value in metadata.values())
    if len(response) < 1980:
        await message.reply(f"```yaml\n{response}```")
    else:
        with io.StringIO() as f:
            f.write(response)
            f.seek(0)
            await message.reply(file=File(f, "parameters.yaml"), mention_author=False)
            await ctx.respond("Done!")
@client.message_command(name="Print Parameters/Prompt")
async def print_params(ctx: ApplicationContext, message: Message):
    """Get a formatted list of parameters for every image in this post."""
    attachments = [a for a in message.attachments if a.filename.lower().endswith(".png")]
    if not attachments:
        await ctx.respond("This post contains no matching images.", ephemeral=True)
        return
    await ctx.defer(ephemeral=True)
    metadata = OrderedDict()
    tasks = [read_attachment_metadata(i, attachment, metadata) for i, attachment in enumerate(attachments)]
    await asyncio.gather(*tasks)
    if not metadata:
        await ctx.respond(f"This post contains no image generation data.", ephemeral=True)
        return
    user_dm = await client.get_user(ctx.user.id).create_dm()
    for attachment, data in [(attachments[i], data) for i, data in metadata.items()]:
        try:
            if 'Steps:' in data:
                try:
                    params = get_params_from_string(data)
                    embed = get_embed(params, message)
                    embed.set_image(url=attachment.url)
                    custom_view = MyView()
                    custom_view.metadata = data
                    await message.reply(view=custom_view, embed=embed, mention_author=False)
                    await ctx.respond("Done!")
                except Exception as e:
                    print(traceback.format_exc())
                    txt = "uh oh! PI-chan did a fucky wucky and cant pawse it into a neat view\n >w<"
                    await ctx.respond(txt)
            elif 'Software' in data:
                if isinstance(data, str):
                    data = json.loads(data)
                embed = get_embed_nai(data, message)
                embed.set_image(url=attachment.url)
                custom_view = MyView()
                custom_view.metadata = data
                await message.reply(view=custom_view, embed=embed, mention_author=False)
            else:
                if "\"inputs\"" not in data:
                    continue

                i = 0
                embed = Embed(title="ComfyUI Parameters", color=discord.Color.from_rgb(*CONFIG.get("EMBED_COLOR")))
                for enum, dax in enumerate(comfyui_get_data(data)):
                    i += 1
                    if i >= 25:
                        break  # why the hell continue? just break...
                    embed.add_field(name=f"{dax['type']} {enum + 1} (beta)", value=dax['val'], inline=True)
                embed.set_footer(text=f'Posted by {message.author}', icon_url=message.author.display_avatar)
                embed.set_image(url=attachment.url)
                await message.reply(embed=embed, mention_author=False)
                with io.StringIO() as f:
                    indented = json.dumps(json.loads(data), sort_keys=True, indent=2)
                    f.write(indented)
                    f.seek(0)
                    await message.reply(file=File(f, "parameters.json"), mention_author=False)
                    await ctx.respond("Done!")

        except Exception:
            print(data)
            print(traceback.format_exc())
            pass
client.run(CONFIG.get("BOT_TOKEN"))
