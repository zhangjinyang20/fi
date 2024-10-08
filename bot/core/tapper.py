import asyncio
import random
from datetime import datetime,timezone
from random import randint
from time import time
from urllib.parse import unquote

import aiocfscrape
import aiohttp
from aiohttp_proxy import ProxyConnector
from better_proxy import Proxy
from pyrogram import Client
from pyrogram.errors import Unauthorized, UserDeactivated, AuthKeyUnregistered, FloodWait
from pyrogram.raw.functions.messages import RequestAppWebView
from pyrogram.raw.types import InputBotAppShortName

from bot.config import settings
from bot.exceptions import InvalidSession, InvalidProtocol
from bot.utils import logger
from bot.utils.boosts import FreeBoostType, UpgradableBoostType
from bot.utils.graphql import Query, OperationName
from bot.utils.scripts import calculate_spin_multiplier
from .TLS import TLSv1_3_BYPASS
from .headers import headers


class Tapper:
    def __init__(self, tg_client: Client):
        self.session_name = tg_client.name
        self.tg_client = tg_client

        self.GRAPHQL_URL = 'https://api-gw-tg.memefi.club/graphql'

    async def get_tg_web_data(self, proxy: str | None):
        if proxy:
            proxy = Proxy.from_str(proxy)
            proxy_dict = dict(
                scheme=proxy.protocol,
                hostname=proxy.host,
                port=proxy.port,
                username=proxy.login,
                password=proxy.password
            )
        else:
            proxy_dict = None

        self.tg_client.proxy = proxy_dict

        try:
            if not self.tg_client.is_connected:
                try:
                    await self.tg_client.connect()
                except (Unauthorized, UserDeactivated, AuthKeyUnregistered):
                    raise InvalidSession(self.session_name)
            while True:
                try:
                    peer = await self.tg_client.resolve_peer('memefi_coin_bot')
                    break
                except FloodWait as fl:
                    fls = fl.value

                    logger.warning(f"{self.session_name} | FloodWait {fl}")
                    logger.info(f"{self.session_name} | Sleep {fls}s")
                    await asyncio.sleep(fls + 3)
            web_view = await self.tg_client.invoke(RequestAppWebView(
                peer=peer,
                platform='android',
                app=InputBotAppShortName(bot_id=peer, short_name="main"),
                write_allowed=True,
                start_param=settings.REF_ID
            ))
            auth_url = web_view.url
            tg_web_data = unquote(
                string=unquote(
                    string=auth_url.split('tgWebAppData=', maxsplit=1)[1].split('&tgWebAppVersion', maxsplit=1)[0]))

            auth_date = tg_web_data.split('auth_date=', maxsplit=1)[1].split('&hash', maxsplit=1)[0]
            hash_ = tg_web_data.split('hash=', maxsplit=1)[1]

            chat_instance = tg_web_data.split('chat_instance=', maxsplit=1)[1].split('&chat_type', maxsplit=1)[0]
            chat_type = tg_web_data.split('chat_type=', maxsplit=1)[1].split('&start_param', maxsplit=1)[0]
            user_data = tg_web_data.split('user=', maxsplit=1)[1].split('&chat_instance', maxsplit=1)[0]
            start_param = tg_web_data.split('start_param=', maxsplit=1)[1].split('&auth_date', maxsplit=1)[0]

            ad = int(auth_date)
            me = await self.tg_client.get_me()
            json_data = [{
                "operationName": "MutationTelegramUserLogin",
                "variables": {
                    "webAppData": {
                        "auth_date": ad,
                        "hash": hash_,
                        "query_id": "",
                        "checkDataString": f"auth_date={ad}\nchat_instance={chat_instance}\nchat_type={chat_type}\nstart_param={start_param}\nuser={user_data}",
                        "user": {
                            "id": me.id,
                            "allows_write_to_pm": True,
                            "first_name": me.first_name,
                            "last_name": me.last_name if me.last_name else '',
                            "username": me.username if me.username else '',
                            "language_code": me.language_code if me.language_code else 'en',
                            "version": "7.4",
                            "platform": "android"
                        }
                    },
                    "referralCode": settings.REF_ID.replace("r_", "")
                },
                "query": "mutation MutationTelegramUserLogin($webAppData: TelegramWebAppDataInput!, $referralCode: String) {\n  telegramUserLogin(webAppData: $webAppData, referralCode: $referralCode) {\n    access_token\n    __typename\n  }\n}"
            }]
            if self.tg_client.is_connected:
                await self.tg_client.disconnect()

            return json_data

        except InvalidSession as error:
            raise error

        except Exception as error:
            logger.error(f"{self.session_name} | ❗️ Unknown error during Authorization: {error}")
            await asyncio.sleep(delay=3)

    async def get_access_token(self, http_client: aiohttp.ClientSession, tg_web_data):
        for _ in range(5):
            try:
                response = await http_client.post(url=self.GRAPHQL_URL, json=tg_web_data)
                response.raise_for_status()

                response_json = await response.json()

                if 'errors' in response_json:
                    raise InvalidProtocol(f'get_access_token msg: {response_json["errors"][0]["message"]}')

                access_token = response_json[0].get('data', {}).get('telegramUserLogin', {}).get('access_token', '')

                if not access_token:
                    await asyncio.sleep(delay=3)
                    continue

                return access_token
            except Exception as error:
                logger.error(f"{self.session_name} | ❗️ Unknown error while getting Access Token: {error}")
                await asyncio.sleep(delay=3)

        return ""

    async def get_telegram_me(self, http_client: aiohttp.ClientSession):
        try:
            json_data = {
                'operationName': OperationName.QueryTelegramUserMe,
                'query': Query.QueryTelegramUserMe,
                'variables': {}
            }

            response = await http_client.post(url=self.GRAPHQL_URL, json=json_data)
            response.raise_for_status()

            response_json = await response.json()

            if 'errors' in response_json:
                raise InvalidProtocol(f'get_telegram_me msg: {response_json["errors"][0]["message"]}')

            me = response_json['data']['telegramUserMe']

            return me
        except Exception as error:
            logger.error(f"{self.session_name} | ❗️ Unknown error while getting Telegram Me: {error}")
            await asyncio.sleep(delay=3)

            return {}

    async def get_profile_data(self, http_client: aiohttp.ClientSession):
        for _ in range(5):
            try:
                json_data = {
                    'operationName': OperationName.QUERY_GAME_CONFIG,
                    'query': Query.QUERY_GAME_CONFIG,
                    'variables': {}
                }

                response = await http_client.post(url=self.GRAPHQL_URL, json=json_data)
                response.raise_for_status()

                response_json = await response.json()

                if 'errors' in response_json:
                    raise InvalidProtocol(f'get_profile_data msg: {response_json["errors"][0]["message"]}')

                profile_data = response_json.get('data', {}).get('telegramGameGetConfig', {})

                if not profile_data:
                    await asyncio.sleep(delay=3)
                    continue

                return profile_data
            except Exception as error:
                logger.error(f"{self.session_name} | ❗️ Unknown error while getting Profile Data: {error}")
                await asyncio.sleep(delay=3)

        return {}

    async def get_bot_config(self, http_client: aiohttp.ClientSession):
        for _ in range(5):
            try:
                json_data = {
                    'operationName': OperationName.TapbotConfig,
                    'query': Query.TapbotConfig,
                    'variables': {}
                }

                response = await http_client.post(url=self.GRAPHQL_URL, json=json_data)
                response.raise_for_status()

                response_json = await response.json()

                if 'errors' in response_json:
                    raise InvalidProtocol(f'get_bot_config msg: {response_json["errors"][0]["message"]}')

                bot_config = response_json.get('data', {}).get('telegramGameTapbotGetConfig', {})

                if not bot_config:
                    await asyncio.sleep(delay=3)
                    continue

                return bot_config
            except Exception as error:
                logger.error(f"{self.session_name} | ❗️ Unknown error while getting TapBot Data: {error}")
                await asyncio.sleep(delay=3)

        return {}

    async def start_bot(self, http_client: aiohttp.ClientSession):
        for _ in range(5):
            try:
                json_data = {
                    'operationName': OperationName.TapbotStart,
                    'query': Query.TapbotStart,
                    'variables': {}
                }

                response = await http_client.post(url=self.GRAPHQL_URL, json=json_data)
                response.raise_for_status()

                response_json = await response.json()

                if 'errors' in response_json:
                    raise InvalidProtocol(f'start_bot msg: {response_json["errors"][0]["message"]}')

                start_data = response_json['data']['telegramGameTapbotStart']

                if not start_data:
                    await asyncio.sleep(delay=3)
                    continue

                return start_data
            except Exception as error:
                logger.error(f"{self.session_name} | ❗️ Unknown error while Starting Bot: {error}")
                await asyncio.sleep(delay=3)

        return None

    async def claim_bot(self, http_client: aiohttp.ClientSession):
        for _ in range(5):
            try:
                json_data = {
                    'operationName': OperationName.TapbotClaim,
                    'query': Query.TapbotClaim,
                    'variables': {}
                }

                response = await http_client.post(url=self.GRAPHQL_URL, json=json_data)
                response.raise_for_status()

                response_json = await response.json()

                if 'errors' in response_json:
                    raise InvalidProtocol(f'claim_bot msg: {response_json["errors"][0]["message"]}')

                claim_data = response_json.get('data', {}).get('telegramGameTapbotClaimCoins', {})

                if not claim_data:
                    await asyncio.sleep(delay=3)
                    continue

                return claim_data
            except Exception as error:
                logger.error(f"{self.session_name} | ❗️ Unknown error while Claiming Bot: {error}")
                await asyncio.sleep(delay=3)

        return {}

    async def set_next_boss(self, http_client: aiohttp.ClientSession):
        try:
            json_data = {
                'operationName': OperationName.telegramGameSetNextBoss,
                'query': Query.telegramGameSetNextBoss,
                'variables': {}
            }

            response = await http_client.post(url=self.GRAPHQL_URL, json=json_data)
            response.raise_for_status()

            response_json = await response.json()

            return True
        except Exception as error:
            logger.error(f"{self.session_name} | ❗️ Unknown error while Setting Next Boss: {error}")
            await asyncio.sleep(delay=3)

            return False

    async def getTask(self, http_client: aiohttp.ClientSession):
        json_data = [{
            "operationName": "CampaignLists",
            "variables": {},
            "query": "fragment FragmentCampaign on CampaignOutput {\n  id\n  type\n  status\n  backgroundImageUrl\n  campaignUserParticipationId\n  completedTotalTasksAmount\n  description\n  endDate\n  iconUrl\n  isStarted\n  name\n  completionReward {\n    spinEnergyReward\n    coinsReward\n    claimedAt\n    id\n    __typename\n  }\n  totalRewardsPool\n  totalTasksAmount\n  collectedRewardsAmount\n  penaltyAmount\n  penaltySpinEnergyAmount\n  collectedSpinEnergyRewardsAmount\n  totalSpinEnergyRewardsPool\n  __typename\n}\n\nquery CampaignLists {\n  campaignLists {\n    special {\n      ...FragmentCampaign\n      __typename\n    }\n    normal {\n      ...FragmentCampaign\n      __typename\n    }\n    archivedCount\n    __typename\n  }\n}"
        }]
        response = await http_client.post(url=self.GRAPHQL_URL, json=json_data)
        response.raise_for_status()
        response_json = await response.json()
        return response_json[0].get('data')['campaignLists']['normal']

    async def apply_boost(self, http_client: aiohttp.ClientSession, boost_type: FreeBoostType):
        try:
            json_data = {
                'operationName': OperationName.telegramGameActivateBooster,
                'query': Query.telegramGameActivateBooster,
                'variables': {
                    'boosterType': boost_type
                }
            }

            response = await http_client.post(url=self.GRAPHQL_URL, json=json_data)
            response.raise_for_status()

            response_json = await response.json()

            if 'errors' in response_json:
                raise InvalidProtocol(f'apply_boost msg: {response_json["errors"][0]["message"]}')

            return True
        except Exception as error:
            logger.error(f"{self.session_name} | ❗️ Unknown error while Apply {boost_type} Boost: {error}")
            await asyncio.sleep(delay=3)

            return False

    async def play_slotmachine(self, http_client: aiohttp.ClientSession, spin_multiplier: int):
        try:
            json_data = {
                'operationName': OperationName.SpinSlotMachine,
                'query': Query.SpinSlotMachine,
                'variables': {'payload': {'spinsCount': spin_multiplier}}
            }

            response = await http_client.post(url=self.GRAPHQL_URL, json=json_data)
            response.raise_for_status()

            response_json = await response.json()

            play_data = response_json.get('data', {}).get('slotMachineSpinV2', {})

            return play_data
        except Exception:
            return {}

    async def upgrade_boost(self, http_client: aiohttp.ClientSession, boost_type: UpgradableBoostType):
        try:
            json_data = {
                'operationName': OperationName.telegramGamePurchaseUpgrade,
                'query': Query.telegramGamePurchaseUpgrade,
                'variables': {
                    'upgradeType': boost_type
                }
            }

            response = await http_client.post(url=self.GRAPHQL_URL, json=json_data)
            response.raise_for_status()

            response_json = await response.json()

            if 'errors' in response_json:
                raise InvalidProtocol(f'upgrade_boost msg: {response_json["errors"][0]["message"]}')

            return True
        except Exception:
            return False

    async def send_taps(self, http_client: aiohttp.ClientSession, nonce: str, taps: int):
        for _ in range(5):
            try:
                vector = []

                for _ in range(taps):
                    vector.append(str(randint(1, 4)))

                vector = ','.join(vector)

                json_data = {
                    'operationName': OperationName.MutationGameProcessTapsBatch,
                    'query': Query.MutationGameProcessTapsBatch,
                    'variables': {
                        'payload': {
                            'nonce': nonce,
                            'tapsCount': taps,
                            'vector': vector,
                        },
                    }
                }

                response = await http_client.post(url=self.GRAPHQL_URL, json=json_data)
                response.raise_for_status()

                response_json = await response.json()

                if 'errors' in response_json:
                    raise InvalidProtocol(f'send_taps msg: {response_json["errors"][0]["message"]}')

                profile_data = response_json.get('data', {}).get('telegramGameProcessTapsBatch', {})

                if not profile_data:
                    await asyncio.sleep(delay=3)
                    continue

                return profile_data
            except Exception as error:
                logger.error(f"{self.session_name} | ❗️ Unknown error when Tapping: {error}")
                await asyncio.sleep(delay=3)

        return {}

    async def start_tapbot(self, http_client: aiohttp.ClientSession, bot_config: dict):
        used_attempts = bot_config.get('usedAttempts', 0)
        total_attempts = bot_config.get('totalAttempts', 0)

        if used_attempts < total_attempts:
            logger.info(f"{self.session_name} | Sleep 5s before start the TapBot")
            await asyncio.sleep(5)

            start_data = await self.start_bot(http_client=http_client)
            if start_data:
                damage_per_sec = start_data.get('damagePerSec', 0)
                logger.success(f"{self.session_name} | Successfully started TapBot | "
                               f"Damage per second: <le>{damage_per_sec}</le> points")
        else:
            logger.info(f"{self.session_name} | TapBot attempts are spent | "
                        f"<ly>{used_attempts}</ly><lw>/</lw><le>{total_attempts}</le>")

    async def purchase_and_start_tapbot(self, http_client: aiohttp.ClientSession, bot_config: dict):
        status = await self.upgrade_boost(http_client=http_client, boost_type=UpgradableBoostType.TAPBOT)
        if status:
            logger.success(f"{self.session_name} | Successfully purchased TapBot")
            await asyncio.sleep(1)
            await self.start_tapbot(http_client, bot_config)

    async def check_proxy(self, http_client: aiohttp.ClientSession, proxy: Proxy) -> None:
        try:
            response = await http_client.get(url='https://api.ipify.org?format=json', timeout=aiohttp.ClientTimeout(5))
            ip = (await response.json()).get('ip')
            logger.info(f"{self.session_name} | Proxy IP: {ip}")
        except Exception as error:
            logger.error(f"{self.session_name} | Proxy: {proxy} | Error: {error}")

    async def run(self, proxy: str | None):
        random_delay = random.randint(0, 15)
        logger.info(f"{self.tg_client.name} | Bot will start in <light-red>{random_delay}s</light-red>")
        await asyncio.sleep(delay=random_delay)
        access_token_created_time = 0
        ends_at_logged_time = 0
        turbo_time = 0
        active_turbo = False

        ssl_context = TLSv1_3_BYPASS.create_ssl_context()
        conn = ProxyConnector().from_url(url=proxy, rdns=True, ssl=ssl_context) if proxy \
            else aiohttp.TCPConnector(ssl=ssl_context)

        async with aiocfscrape.CloudflareScraper(headers=headers, connector=conn) as http_client:
            if proxy:
                await self.check_proxy(http_client=http_client, proxy=proxy)

            while True:
                try:
                    if time() - access_token_created_time >= 5400:
                        http_client.headers.pop("Authorization", None)

                        tg_web_data = await self.get_tg_web_data(proxy=proxy)

                        if not tg_web_data:
                            logger.info(f"{self.session_name} | Log out!")
                            return

                        access_token = await self.get_access_token(http_client=http_client, tg_web_data=tg_web_data)

                        if not access_token:
                            await asyncio.sleep(delay=5)
                            continue

                        http_client.headers["Authorization"] = f"Bearer {access_token}"

                        access_token_created_time = time()
                        random_delay = random.randint(0, 15)
                        logger.info(f"{self.tg_client.name} | Bot will start in <light-red>{random_delay}s</light-red>")
                        await asyncio.sleep(delay=random_delay)
                        await self.get_telegram_me(http_client=http_client)

                        profile_data = await self.get_profile_data(http_client=http_client)
                        if not profile_data:
                            continue
                        balance = profile_data.get('coinsAmount', 0)

                        nonce = profile_data.get('nonce', '')

                        current_boss = profile_data.get('currentBoss', {})
                        current_boss_level = current_boss.get('level', 0)
                        boss_max_health = current_boss.get('maxHealth', 0)
                        boss_current_health = current_boss.get('currentHealth', 0)

                        logger.info(f"{self.session_name} | Current boss level: <lm>{current_boss_level:,}</lm> | "
                                    f"Boss health: <lr>{boss_current_health:,}</lr><lw>/</lw><le>{boss_max_health:,}</le>")

                        await asyncio.sleep(delay=.5)

                    spins = profile_data.get('spinEnergyTotal', 0)
                    while spins > 0:
                        await asyncio.sleep(delay=1)

                        spin_multiplier = calculate_spin_multiplier(spins=spins)
                        play_data = await self.play_slotmachine(http_client=http_client,
                                                                spin_multiplier=spin_multiplier)

                        reward_amount = play_data.get('spinResults', [{}])[0].get('rewardAmount', 0)
                        reward_type = play_data.get('spinResults', [{}])[0].get('rewardType', 'NO')
                        spins = play_data.get('gameConfig', {}).get('spinEnergyTotal', 0)
                        balance = play_data.get('gameConfig', {}).get('coinsAmount', 0)

                        logger.info(f"{self.session_name} | Successfully played in slot machine | "
                                    f"Balance: <lc>{balance:,}</lc> (<lg>+{reward_amount:,}</lg> <lm>{reward_type}</lm>) | "
                                    f"Spins: <le>{spins:,}</le> (<lr>-{spin_multiplier:,}</lr>)")

                        await asyncio.sleep(delay=1)

                    taps = randint(a=settings.RANDOM_TAPS_COUNT[0], b=settings.RANDOM_TAPS_COUNT[1])

                    available_energy = profile_data.get('currentEnergy', 0)
                    need_energy = taps * profile_data.get('weaponLevel', 0)

                    if active_turbo:
                        taps += settings.ADD_TAPS_ON_TURBO
                        need_energy = 0
                        if time() - turbo_time > 10:
                            active_turbo = False
                            turbo_time = 0

                    if need_energy > available_energy:
                        logger.warning(f"{self.session_name} | "
                                       f"Need more energy: <ly>{available_energy:,}</ly>"
                                       f"<lw>/</lw><le>{need_energy:,}</le> for <lg>{taps:,}</lg> taps")

                        sleep_between_clicks = randint(a=settings.SLEEP_BETWEEN_TAP[0], b=settings.SLEEP_BETWEEN_TAP[1])

                        logger.info(f"Sleep <lw>{sleep_between_clicks:,}</lw>s")
                        await asyncio.sleep(delay=sleep_between_clicks)

                        profile_data = await self.get_profile_data(http_client=http_client)

                        continue

                    profile_data = await self.send_taps(http_client=http_client, nonce=nonce, taps=taps)

                    if not profile_data:
                        continue

                    available_energy = profile_data.get('currentEnergy', 0)
                    new_balance = profile_data.get('coinsAmount', 0)
                    calc_taps = new_balance - balance
                    balance = new_balance

                    free_boosts = profile_data.get('freeBoosts', {})
                    turbo_boost_count = free_boosts.get('currentTurboAmount', 0)
                    energy_boost_count = free_boosts.get('currentRefillEnergyAmount', 0)

                    next_tap_level = profile_data.get('weaponLevel', 0) + 1
                    next_energy_level = profile_data.get('energyLimitLevel', 0) + 1
                    next_charge_level = profile_data.get('energyRechargeLevel', 0) + 1

                    nonce = profile_data.get('nonce', '')

                    current_boss = profile_data.get('currentBoss', {})
                    current_boss_level = current_boss.get('level', 0)
                    boss_current_health = current_boss.get('currentHealth', 0)

                    logger.success(f"{self.session_name} | Successful tapped! | "
                                   f"Balance: <lc>{balance:,}</lc> (<lg>+{calc_taps}</lg>) | "
                                   f"Boss health: <lr>{boss_current_health:,}</lr> | "
                                   f"Energy: <ly>{available_energy:,}</ly>")

                    if boss_current_health <= 0:
                        logger.info(f"{self.session_name} | Setting next boss: <lm>{current_boss_level + 1}</lm> lvl")

                        status = await self.set_next_boss(http_client=http_client)
                        if status is True:
                            logger.success(f"{self.session_name} | Successful setting next boss: "
                                           f"<lm>{current_boss_level + 1}</lm>")

                        continue

                    if active_turbo is False:
                        if (energy_boost_count > 0
                                and available_energy < settings.MIN_AVAILABLE_ENERGY
                                and settings.APPLY_DAILY_ENERGY is True):
                            logger.info(f"{self.session_name} | Sleep <lw>5s</lw> before activating daily energy boost")
                            await asyncio.sleep(delay=5)

                            status = await self.apply_boost(http_client=http_client, boost_type=FreeBoostType.ENERGY)
                            if status is True:
                                logger.success(f"{self.session_name} | Energy boost applied")

                                await asyncio.sleep(delay=1)

                            continue

                        if turbo_boost_count > 0 and settings.APPLY_DAILY_TURBO is True:
                            logger.info(f"{self.session_name} | Sleep <lw>5s</lw> before activating daily turbo boost")
                            await asyncio.sleep(delay=5)

                            status = await self.apply_boost(http_client=http_client, boost_type=FreeBoostType.TURBO)
                            if status is True:
                                logger.success(f"{self.session_name} | Turbo boost applied")

                                await asyncio.sleep(delay=1)

                                active_turbo = True
                                turbo_time = time()

                            continue

                        if settings.USE_TAP_BOT is True:
                            bot_config = await self.get_bot_config(http_client=http_client)

                            is_purchased = bot_config.get('isPurchased', False)
                            ends_at = bot_config.get('endsAt', None)

                            if not ends_at:
                                if is_purchased:
                                    await self.start_tapbot(http_client, bot_config)
                                else:
                                    await self.purchase_and_start_tapbot(http_client, bot_config)
                            else:
                                ends_at_date = datetime.strptime(ends_at, '%Y-%m-%dT%H:%M:%S.%f%z')
                                custom_ends_at_date = ends_at_date.strftime('%d.%m.%Y %H:%M:%S')
                                ends_at_timestamp = ends_at_date.timestamp()

                                if ends_at_logged_time <= time():
                                    logger.info(f"{self.session_name} | TapBot ends at: <ly>{custom_ends_at_date}</ly>")
                                    ends_at_logged_time = time() + 900

                                if ends_at_timestamp < time():
                                    logger.info(f"{self.session_name} | Sleep <lw>5s</lw> before claim TapBot")
                                    await asyncio.sleep(5)

                                    claim_data = await self.claim_bot(http_client=http_client)
                                    if claim_data:
                                        logger.success(f"{self.session_name} | Successfully claimed TapBot")
                                        await self.start_tapbot(http_client, bot_config)
                                elif not is_purchased:
                                    await self.purchase_and_start_tapbot(http_client, bot_config)

                        if settings.AUTO_UPGRADE_TAP is True and next_tap_level <= settings.MAX_TAP_LEVEL:
                            need_balance = 1000 * (2 ** (next_tap_level - 1))

                            if balance > need_balance:
                                status = await self.upgrade_boost(http_client=http_client,
                                                                  boost_type=UpgradableBoostType.TAP)
                                if status is True:
                                    logger.success(f"{self.session_name} | "
                                                   f"Tap upgraded to <lm>{next_tap_level}</lm> lvl")

                                    await asyncio.sleep(delay=1)
                            else:
                                logger.warning(f"{self.session_name} | "
                                               f"Need more gold for upgrade tap to <lm>{next_tap_level}</lm> lvl "
                                               f"(<lc>{balance}</lc><lw>/</lw><le>{need_balance}</le>)")

                        if settings.AUTO_UPGRADE_ENERGY is True and next_energy_level <= settings.MAX_ENERGY_LEVEL:
                            need_balance = 1000 * (2 ** (next_energy_level - 1))
                            if balance > need_balance:
                                status = await self.upgrade_boost(http_client=http_client,
                                                                  boost_type=UpgradableBoostType.ENERGY)
                                if status is True:
                                    logger.success(f"{self.session_name} | "
                                                   f"Energy upgraded to <lm>{next_energy_level}</lm> lvl")

                                    await asyncio.sleep(delay=1)
                            else:
                                logger.warning(f"{self.session_name} | "
                                               f"Need more gold for upgrade energy to <lm>{next_energy_level}</lm> lvl "
                                               f"(<lc>{balance}</lc><lw>/</lw><le>{need_balance}</le>)")

                        if settings.AUTO_UPGRADE_CHARGE is True and next_charge_level <= settings.MAX_CHARGE_LEVEL:
                            need_balance = 1000 * (2 ** (next_charge_level - 1))

                            if balance > need_balance:
                                status = await self.upgrade_boost(http_client=http_client,
                                                                  boost_type=UpgradableBoostType.CHARGE)
                                if status is True:
                                    logger.success(f"{self.session_name} | "
                                                   f"Charge upgraded to <lm>{next_charge_level}</lm> lvl")

                                    await asyncio.sleep(delay=1)
                            else:
                                logger.warning(f"{self.session_name} | "
                                               f"Need more gold for upgrade charge to <lm>{next_energy_level}</lm> lvl "
                                               f"(<lc>{balance}</lc><lw>/</lw><le>{need_balance}</le>)")
                        # 获取任务
                        random_delay = random.randint(15, 75)
                        logger.info(f"{self.tg_client.name} | Bot will start in <light-red>{random_delay}s</light-red>")
                        await asyncio.sleep(delay=random_delay)
                        taskList = await self.getTask(http_client=http_client)
                        for ts in taskList:
                            if '▶' in ts['description'] and ts['isStarted'] is False:
                                # 开始做任务
                                params = [{
                                    "operationName": "GetCampaignById",
                                    "variables": {
                                        "campaignId": ts['id']
                                    },
                                    "query": "fragment FragmentCampaign on CampaignOutput {\n  id\n  type\n  status\n  backgroundImageUrl\n  campaignUserParticipationId\n  completedTotalTasksAmount\n  description\n  endDate\n  iconUrl\n  isStarted\n  name\n  completionReward {\n    spinEnergyReward\n    coinsReward\n    claimedAt\n    id\n    __typename\n  }\n  totalRewardsPool\n  totalTasksAmount\n  collectedRewardsAmount\n  penaltyAmount\n  penaltySpinEnergyAmount\n  collectedSpinEnergyRewardsAmount\n  totalSpinEnergyRewardsPool\n  __typename\n}\n\nquery GetCampaignById($campaignId: String!) {\n  campaignGetById(campaignId: $campaignId) {\n    ...FragmentCampaign\n    __typename\n  }\n}"
                                }]
                                random_delay = random.randint(15, 75)
                                logger.info(
                                    f"{self.tg_client.name} | Bot will start in <light-red>{random_delay}s</light-red>")
                                await asyncio.sleep(delay=random_delay)
                                GetCampaignById_response = await http_client.post(url=self.GRAPHQL_URL,
                                                                                  json=params)
                                GetCampaignById_response.raise_for_status()
                                GetCampaignById_response_json = await GetCampaignById_response.json()
                                if GetCampaignById_response_json[0].get('data')['campaignGetById'][
                                    'isStarted'] is False:
                                    random_delay = random.randint(15, 75)
                                    logger.info(
                                        f"{self.tg_client.name} | Bot will start in <light-red>{random_delay}s</light-red>")
                                    await asyncio.sleep(delay=random_delay)
                                    GetTasksList = [{
                                        "operationName": "GetTasksList",
                                        "variables": {
                                            "campaignId": ts['id']
                                        },
                                        "query": "fragment FragmentCampaignTask on CampaignTaskOutput {\n  id\n  name\n  description\n  status\n  type\n  position\n  buttonText\n  coinsRewardAmount\n  spinEnergyRewardAmount\n  link\n  userTaskId\n  isRequired\n  iconUrl\n  taskVerificationType\n  verificationAvailableAt\n  shouldUseVpn\n  isLinkInternal\n  quiz {\n    id\n    question\n    answers\n    __typename\n  }\n  __typename\n}\n\nquery GetTasksList($campaignId: String!) {\n  campaignTasks(campaignConfigId: $campaignId) {\n    ...FragmentCampaignTask\n    __typename\n  }\n}"
                                    }]
                                    GetTasksList_response = await http_client.post(url=self.GRAPHQL_URL,
                                                                                   json=GetTasksList)
                                    GetTasksList_response.raise_for_status()
                                    GetTasksList_response_json = await GetTasksList_response.json()
                                    _id = GetTasksList_response_json[0].get('data')['campaignTasks'][0]['id']
                                    random_delay = random.randint(15, 75)
                                    logger.info(
                                        f"{self.tg_client.name} | Bot will start in <light-red>{random_delay}s</light-red>")
                                    await asyncio.sleep(delay=random_delay)
                                    GetTaskById = [{
                                        "operationName": "GetTaskById",
                                        "variables": {
                                            "taskId": _id
                                        },
                                        "query": "fragment FragmentCampaignTask on CampaignTaskOutput {\n  id\n  name\n  description\n  status\n  type\n  position\n  buttonText\n  coinsRewardAmount\n  spinEnergyRewardAmount\n  link\n  userTaskId\n  isRequired\n  iconUrl\n  taskVerificationType\n  verificationAvailableAt\n  shouldUseVpn\n  isLinkInternal\n  quiz {\n    id\n    question\n    answers\n    __typename\n  }\n  __typename\n}\n\nquery GetTaskById($taskId: String!) {\n  campaignTaskGetConfig(taskId: $taskId) {\n    ...FragmentCampaignTask\n    __typename\n  }\n}"
                                    }, {
                                        "operationName": "TwitterProfile",
                                        "variables": {},
                                        "query": "query TwitterProfile {\n  twitterProfile {\n    username\n    __typename\n  }\n}"
                                    }
                                    ]
                                    GetTaskById_response = await http_client.post(url=self.GRAPHQL_URL,
                                                                                  json=GetTaskById)
                                    GetTaskById_response.raise_for_status()
                                    GetTaskById_response_json = await GetTaskById_response.json()
                                    GetTaskById_response_json_f = GetTaskById_response_json[0].get('data')[
                                        'campaignTaskGetConfig']
                                    if GetTaskById_response_json_f['status'] == 'Pending':
                                        random_delay = random.randint(15, 75)
                                        logger.info(
                                            f"{self.tg_client.name} | Bot will start in <light-red>{random_delay}s</light-red>")
                                        await asyncio.sleep(delay=random_delay)
                                        CampaignTaskToVerification = [{
                                            "operationName": "CampaignTaskToVerification",
                                            "variables": {
                                                "taskConfigId": _id,
                                            },
                                            "query": "fragment FragmentCampaignTask on CampaignTaskOutput {\n  id\n  name\n  description\n  status\n  type\n  position\n  buttonText\n  coinsRewardAmount\n  spinEnergyRewardAmount\n  link\n  userTaskId\n  isRequired\n  iconUrl\n  taskVerificationType\n  verificationAvailableAt\n  shouldUseVpn\n  isLinkInternal\n  quiz {\n    id\n    question\n    answers\n    __typename\n  }\n  __typename\n}\n\nmutation CampaignTaskToVerification($taskConfigId: String!) {\n  campaignTaskMoveToVerificationV2(taskConfigId: $taskConfigId) {\n    ...FragmentCampaignTask\n    __typename\n  }\n}"
                                        }]
                                        await http_client.post(url=self.GRAPHQL_URL,
                                                               json=CampaignTaskToVerification)
                                        GetTaskById_two = [{
                                            "operationName": "GetTaskById",
                                            "variables": {
                                                "taskId": _id,
                                            },
                                            "query": "fragment FragmentCampaignTask on CampaignTaskOutput {\n  id\n  name\n  description\n  status\n  type\n  position\n  buttonText\n  coinsRewardAmount\n  spinEnergyRewardAmount\n  link\n  userTaskId\n  isRequired\n  iconUrl\n  taskVerificationType\n  verificationAvailableAt\n  shouldUseVpn\n  isLinkInternal\n  quiz {\n    id\n    question\n    answers\n    __typename\n  }\n  __typename\n}\n\nquery GetTaskById($taskId: String!) {\n  campaignTaskGetConfig(taskId: $taskId) {\n    ...FragmentCampaignTask\n    __typename\n  }\n}"
                                        }, {
                                            "operationName": "TwitterProfile",
                                            "variables": {},
                                            "query": "query TwitterProfile {\n  twitterProfile {\n    username\n    __typename\n  }\n}"
                                        }]
                                        GetTaskById_2_response = await http_client.post(url=self.GRAPHQL_URL,
                                                                                        json=GetTaskById_two)
                                        GetTaskById_2_response.raise_for_status()
                                        GetTaskById_2_response_json = await GetTaskById_2_response.json()
                                        GetTaskById_2_response_json_f = \
                                        GetTaskById_2_response_json[0].get('data')['campaignTaskGetConfig']
                                        if GetTaskById_2_response_json_f['status'] == 'Verification':
                                            random_delay = random.randint(15, 75)
                                            logger.info(
                                                f"{self.tg_client.name} | Bot will start in <light-red>{random_delay}s</light-red>")
                                            await asyncio.sleep(delay=random_delay)
                                            userTaskId = GetTaskById_2_response_json_f['userTaskId']
                                            sleep_time = datetime.strptime(
                                                GetTaskById_2_response_json_f['verificationAvailableAt'].split(
                                                    '.')[0] + 'Z',
                                                "%Y-%m-%dT%H:%M:%SZ").timestamp() - datetime.now().timestamp() + 10
                                            if sleep_time < 0:
                                                continue
                                            logger.info(
                                                f"{self.session_name}|{GetTaskById_2_response_json_f['name']}睡眠{int(sleep_time)}!")
                                            await asyncio.sleep(delay=int(sleep_time))
                                            random_delay = random.randint(15, 75)
                                            logger.info(
                                                f"{self.tg_client.name} | Bot will start in <light-red>{random_delay}s</light-red>")
                                            await asyncio.sleep(delay=random_delay)
                                            CampaignTaskMarkAsCompleted = [{
                                                "operationName": "CampaignTaskMarkAsCompleted",
                                                "variables": {
                                                    "userTaskId": userTaskId,
                                                },
                                                "query": "fragment FragmentCampaignTask on CampaignTaskOutput {\n  id\n  name\n  description\n  status\n  type\n  position\n  buttonText\n  coinsRewardAmount\n  spinEnergyRewardAmount\n  link\n  userTaskId\n  isRequired\n  iconUrl\n  taskVerificationType\n  verificationAvailableAt\n  shouldUseVpn\n  isLinkInternal\n  quiz {\n    id\n    question\n    answers\n    __typename\n  }\n  __typename\n}\n\nmutation CampaignTaskMarkAsCompleted($userTaskId: String!, $verificationCode: String, $quizAnswers: [CampaignTaskQuizQuestionInput!]) {\n  campaignTaskMarkAsCompleted(\n    userTaskId: $userTaskId\n    verificationCode: $verificationCode\n    quizAnswers: $quizAnswers\n  ) {\n    ...FragmentCampaignTask\n    __typename\n  }\n}"
                                            }]
                                            GetTaskById_2_response = await http_client.post(
                                                url=self.GRAPHQL_URL,
                                                json=CampaignTaskMarkAsCompleted)
                                            GetTaskById_2_response.raise_for_status()
                                            logger.info(f"{self.session_name}|{GetTaskById_2_response_json_f['name']}任务完成!")
                                            break

                        if available_energy < settings.MIN_AVAILABLE_ENERGY:
                            logger.info(f"{self.session_name} | Minimum energy reached: <ly>{available_energy:,}</ly>")

                            if isinstance(settings.SLEEP_BY_MIN_ENERGY, list):
                                sleep_time = randint(a=settings.SLEEP_BY_MIN_ENERGY[0],
                                                     b=settings.SLEEP_BY_MIN_ENERGY[1])
                            else:
                                sleep_time = settings.SLEEP_BY_MIN_ENERGY

                            logger.info(f"{self.session_name} | Sleep <lw>{sleep_time:,}s</lw>")
                            await asyncio.sleep(delay=sleep_time)

                except InvalidProtocol as error:
                    if settings.EMERGENCY_STOP is True:
                        raise error
                    else:
                        logger.error(f"{self.session_name} | ⚠ Warning! Invalid protocol detected in {error}")
                        await asyncio.sleep(delay=randint(a=3, b=7))

                except InvalidSession as error:
                    raise error

                except Exception as error:
                    logger.error(f"{self.session_name} | ❗️ Unknown error: {error}")
                    await asyncio.sleep(delay=3)

                else:
                    sleep_between_clicks = randint(a=settings.SLEEP_BETWEEN_TAP[0], b=settings.SLEEP_BETWEEN_TAP[1])

                    if active_turbo is True:
                        sleep_between_clicks = 4

                    logger.info(f"Sleep {sleep_between_clicks}s")
                    await asyncio.sleep(delay=sleep_between_clicks)


async def run_tapper(tg_client: Client, proxy: str | None):
    try:
        await Tapper(tg_client=tg_client).run(proxy=proxy)
    except InvalidProtocol as error:
        logger.error(f"{tg_client.name} | Invalid protocol detected at {error}")
    except InvalidSession:
        logger.error(f"{tg_client.name} | Invalid Session")
