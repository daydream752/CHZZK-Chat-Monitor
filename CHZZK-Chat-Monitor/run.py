import argparse
import datetime
import logging
import json
import os
from collections import deque
import re
from dotenv import load_dotenv
import api
from broadcast_logger import fetch_start_time, format_duration

from websocket import WebSocket
from cmd_type import CHZZK_CHAT_CMD
from config_store import load_config, load_counts, save_counts


class ChzzkChat:

    def __init__(self, streamer, cookies, logger):

        self.streamer = streamer
        self.cookies  = cookies
        self.logger   = logger

        self._stop = False

        config = load_config()

        self.keyword_display: dict[str, str] = {}
        self.keywords: set[str] = set()

        # 1) Load keywords (stored config + env overrides)
        env_keywords = (os.getenv('KEYWORDS') or '').strip()
        kw_from_env = [k.strip() for k in env_keywords.split(',') if k.strip()]
        stored_keywords = config.get('keywords') or []
        keywords_combined = stored_keywords + kw_from_env
        for raw_kw in keywords_combined:
            kw_str = str(raw_kw).strip()
            if not kw_str:
                continue
            kw_lower = kw_str.lower()
            if not kw_lower:
                continue
            if kw_lower not in self.keyword_display:
                self.keyword_display[kw_lower] = kw_str
            self.keywords.add(kw_lower)

        # 2) Load global threshold: config > env > default 1
        self.keyword_threshold = config.get('global_threshold') or 1
        try:
            env_thr = os.getenv('KEYWORD_THRESHOLD')
            if env_thr and str(env_thr).isdigit():
                self.keyword_threshold = max(1, int(env_thr))
        except Exception:
            pass

        # 3) Load per-keyword thresholds: config map > env JSON
        self.keyword_thresholds: dict[str, int] = {
            str(k).lower(): max(1, int(v))
            for k, v in (config.get('per_keyword_thresholds') or {}).items()
            if str(k).strip()
        }
        try:
            env_map = os.getenv('KEYWORD_THRESHOLDS')
            if env_map:
                data = json.loads(env_map)
                if isinstance(data, dict):
                    for k, v in data.items():
                        if str(v).isdigit():
                            self.keyword_thresholds[str(k).lower()] = max(1, int(v))
        except Exception:
            pass

        # 4) Load global window seconds: config > env > default 60
        self.keyword_window = config.get('global_window') or 60
        try:
            env_win = os.getenv('KEYWORD_WINDOW')
            if env_win and str(env_win).isdigit():
                self.keyword_window = max(1, int(env_win))
        except Exception:
            pass

        # 5) Load per-keyword windows: config map > env JSON
        self.keyword_windows: dict[str, int] = {
            str(k).lower(): max(1, int(v))
            for k, v in (config.get('per_keyword_windows') or {}).items()
            if str(k).strip()
        }
        try:
            env_wmap = os.getenv('KEYWORD_WINDOWS')
            if env_wmap:
                data = json.loads(env_wmap)
                if isinstance(data, dict):
                    for k, v in data.items():
                        if str(v).isdigit():
                            self.keyword_windows[str(k).lower()] = max(1, int(v))
        except Exception:
            pass

        contains_entries = config.get('contains_keywords') or []
        self.contains_keywords_list: list[str] = []
        self.contains_keywords_set: set[str] = set()
        for entry in contains_entries:
            try:
                raw_kw = entry.get('keyword')
            except AttributeError:
                continue
            kw_str = str(raw_kw).strip()
            if not kw_str:
                continue
            kw_lower = kw_str.lower()
            if not kw_lower:
                continue
            if kw_lower not in self.keyword_display:
                self.keyword_display[kw_lower] = kw_str
            if kw_lower not in self.contains_keywords_set:
                self.contains_keywords_list.append(kw_lower)
                self.contains_keywords_set.add(kw_lower)
            thr_val = entry.get('threshold')
            win_val = entry.get('window')
            if thr_val is not None:
                try:
                    self.keyword_thresholds[kw_lower] = max(1, int(thr_val))
                except Exception:
                    pass
            if win_val is not None:
                try:
                    self.keyword_windows[kw_lower] = max(1, int(win_val))
                except Exception:
                    pass
            if kw_lower not in self.keyword_thresholds:
                self.keyword_thresholds[kw_lower] = self.keyword_threshold
            if kw_lower not in self.keyword_windows:
                self.keyword_windows[kw_lower] = self.keyword_window
            self.keywords.add(kw_lower)

        # Ensure per-key configs keys are included in keywords
        if self.keyword_thresholds:
            for key in self.keyword_thresholds.keys():
                self.keywords.add(key)
        if self.keyword_windows:
            for key in self.keyword_windows.keys():
                self.keywords.add(key)
        for key in list(self.keywords):
            if key not in self.keyword_display:
                self.keyword_display[key] = key

        # Compile word-boundary regex patterns for exact-match keywords only
        self.keyword_patterns: dict[str, re.Pattern] = {}
        for kw in self.keywords:
            if kw in self.contains_keywords_set:
                continue
            escaped = re.escape(kw)
            pattern = re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)
            self.keyword_patterns[kw] = pattern

        # Initialize counters and sliding window deques
        stored_counts = load_counts()
        self.keyword_counts = {k: stored_counts.get(k, 0) for k in self.keywords}  # cumulative
        self.keyword_hits_window: dict[str, deque] = {k: deque() for k in self.keywords}
        self.keyword_log = open('keyword_times.log', 'a', encoding='utf-8') if self.keywords else None

        self.broadcast_start_timestamp: float | None = None

        # Debug setup
        try:
            if self.keyword_thresholds:
                self.logger.info(f"[KEYSETUP] per-key thresholds: {self.keyword_thresholds}")
            if self.keyword_windows:
                self.logger.info(f"[KEYSETUP] per-key windows: {self.keyword_windows}")
            display_keywords = sorted(self.keyword_display.get(k, k) for k in self.keywords)
            self.logger.info(f"[KEYSETUP] global threshold={self.keyword_threshold}, window={self.keyword_window}s, keywords={display_keywords}")
        except Exception:
            pass

        self.sid           = None
        self.userIdHash    = api.fetch_userIdHash(self.cookies)
        self.chatChannelId = api.fetch_chatChannelId(self.streamer, self.cookies)
        self.channelName   = api.fetch_channelName(self.streamer)
        self.accessToken, self.extraToken = api.fetch_accessToken(self.chatChannelId, self.cookies)

        self.update_broadcast_start_time()
        self.connect()


    def connect(self):

        self.chatChannelId = api.fetch_chatChannelId(self.streamer, self.cookies)
        self.accessToken, self.extraToken = api.fetch_accessToken(self.chatChannelId, self.cookies)

        sock = WebSocket()
        sock.connect('wss://kr-ss1.chat.naver.com/chat')
        print(f'{self.channelName} 채팅창에 연결 중 .', end="")

        default_dict = {  
            "ver"   : "2",
            "svcid" : "game",
            "cid"   : self.chatChannelId,
        }

        send_dict = {
            "cmd"   : CHZZK_CHAT_CMD['connect'],
            "tid"   : 1,
            "bdy"   : {
                "uid"     : self.userIdHash,
                "devType" : 2001,
                "accTkn"  : self.accessToken,
                "auth"    : "SEND"
            }
        }

        sock.send(json.dumps(dict(send_dict, **default_dict)))
        sock_response = json.loads(sock.recv())
        self.sid = sock_response['bdy']['sid']
        print(f'\r{self.channelName} 채팅창에 연결 중 ..', end="")

        send_dict = {
            "cmd"   : CHZZK_CHAT_CMD['request_recent_chat'],
            "tid"   : 2,
            
            "sid"   : self.sid,
            "bdy"   : {
                "recentMessageCount" : 50
            }
        }

        sock.send(json.dumps(dict(send_dict, **default_dict)))
        sock.recv()
        print(f'\r{self.channelName} 채팅창에 연결 중 ...')

        self.sock = sock
        if self.sock.connected:
            print('연결 완료')
            try:
                self.logger.info('[SYSTEM] 연결이 완료되었습니다.')
            except Exception:
                pass
            self.update_broadcast_start_time()
        else:
            raise ValueError('오류 발생')
        

    def send(self, message:str):

        default_dict = {  
            "ver"   : 2,
            "svcid" : "game",
            "cid"   : self.chatChannelId,
        }

        extras = {
            "chatType"          : "STREAMING",
            "emojis"            : "",
            "osType"            : "PC",
            "extraToken"        : self.extraToken,
            "streamingChannelId": self.chatChannelId
        }

        send_dict = {
            "tid"   : 3,
            "cmd"   : CHZZK_CHAT_CMD['send_chat'],
            "retry" : False,
            "sid"   : self.sid,
            "bdy"   : {
                "msg"           : message,
                "msgTypeCode"   : 1,
                "extras"        : json.dumps(extras),
                "msgTime"       : int(datetime.datetime.now().timestamp())
            }
        }

        self.sock.send(json.dumps(dict(send_dict, **default_dict)))


    def run(self):

        while not self._stop:

            try:
        
                try:
                    raw_message = self.sock.recv()

                except KeyboardInterrupt:
                    break 

                except:
                    if self._stop:
                        break
                    self.connect()
                    raw_message = self.sock.recv()

                if self._stop:
                    break

                raw_message = json.loads(raw_message)
                chat_cmd    = raw_message['cmd']
                
                if chat_cmd == CHZZK_CHAT_CMD['ping']:

                    self.sock.send(
                        json.dumps({
                            "ver" : "2",
                            "cmd" : CHZZK_CHAT_CMD['pong']
                        })
                    )

                    if self.chatChannelId != api.fetch_chatChannelId(self.streamer, self.cookies): # 방송 시작시 chatChannelId가 달라지는 문제
                        self.connect()

                    continue
                
                if chat_cmd == CHZZK_CHAT_CMD['chat']:
                    chat_type = '채팅'

                elif chat_cmd == CHZZK_CHAT_CMD['donation']:
                    chat_type = '후원'

                else:
                    continue

                for chat_data in raw_message['bdy']:
                    
                    if chat_data['uid'] == 'anonymous':
                        nickname = '익명의 후원자'

                    else:
                        
                        try:
                            profile_data = json.loads(chat_data['profile'])
                            nickname = profile_data["nickname"]

                            if 'msg' not in chat_data:
                                continue

                        except:
                            continue

                    now = datetime.datetime.fromtimestamp(chat_data['msgTime']/1000)
                    now = datetime.datetime.strftime(now, '%Y-%m-%d %H:%M:%S')

                    msg_text = chat_data["msg"]
                    self.logger.info(f'[{now}][{chat_type}] {nickname} : {msg_text}')

                    # keyword hit recording with sliding window
                    if self.keywords and isinstance(msg_text, str):
                        text = msg_text
                        text_lower = text.lower()
                        hit = None
                        for kw, pat in self.keyword_patterns.items():
                            if pat.search(text):
                                hit = kw
                                break
                        if not hit:
                            for kw in self.contains_keywords_list:
                                if kw in text_lower:
                                    hit = kw
                                    break
                        if hit:
                            try:
                                # cumulative
                                self.keyword_counts[hit] = self.keyword_counts.get(hit, 0) + 1

                                # sliding window
                                ts = chat_data['msgTime'] / 1000.0
                                window_sec = self.keyword_windows.get(hit, self.keyword_window)
                                dq = self.keyword_hits_window.setdefault(hit, deque())
                                dq.append(ts)
                                # pop old
                                cutoff = ts - window_sec
                                while dq and dq[0] < cutoff:
                                    dq.popleft()
                                window_count = len(dq)

                                thr = self.keyword_thresholds.get(hit, self.keyword_threshold)
                                if window_count >= thr:
                                    # persist cumulative counts
                                    save_counts(self.keyword_counts)
                                    keyword_label = self.keyword_display.get(hit, hit)
                                    # log hit meeting windowed threshold
                                    self.logger.info(f"[KEYCOUNT] {keyword_label} window={window_sec}s count={window_count} (threshold={thr})")
                                    if self.keyword_log:
                                        duration_text = "-"
                                        try:
                                            if self.broadcast_start_timestamp is not None:
                                                ts_seconds = chat_data['msgTime'] / 1000.0
                                                elapsed = max(0, int(ts_seconds - self.broadcast_start_timestamp))
                                                duration_text = format_duration(elapsed)
                                        except Exception:
                                            pass
                                        self.keyword_log.write(f'{now}\t{self.channelName}\t{keyword_label}\t{nickname}\t{duration_text}\t{msg_text}\n')
                                        self.keyword_log.flush()
                            except Exception:
                                pass
                
            except:
                if self._stop:
                    break
                pass
            
    def close(self):
        try:
            self._stop = True
            if hasattr(self, 'sock') and self.sock and self.sock.connected:
                self.sock.close()
            if hasattr(self, 'keyword_log') and self.keyword_log:
                try:
                    self.keyword_log.close()
                except Exception:
                    pass
        except Exception:
            pass

    def stop(self):
        self._stop = True
        self.close()

    def update_broadcast_start_time(self):
        try:
            start_dt = fetch_start_time(self.streamer)
            self.broadcast_start_timestamp = start_dt.timestamp()
            try:
                self.logger.info(f"[BROADCAST] 시작 시각: {start_dt.isoformat()}")
            except Exception:
                pass
        except Exception as exc:
            self.broadcast_start_timestamp = None
            try:
                self.logger.warning(f"[BROADCAST] 방송 시작 시각을 가져오지 못했습니다: {exc}")
            except Exception:
                pass

def get_logger():

    formatter = logging.Formatter('%(message)s')

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    file_handler = logging.FileHandler('chat.log', mode = "w")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger


if __name__ == '__main__':

    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument('--streamer_id', type=str, required=False, help='치지직 채널 ID (미지정 시 환경변수 CHANNEL_ID 사용)')
    args = parser.parse_args()

    streamer_id = args.streamer_id or os.getenv('CHANNEL_ID')
    if not streamer_id:
        parser.error('--streamer_id 인자 또는 환경변수 CHANNEL_ID 중 하나를 지정하세요.')

    with open('cookies.json') as f:
        cookies = json.load(f)

    logger = get_logger()
    chzzkchat = ChzzkChat(streamer_id, cookies, logger)

    try:
        # 채팅 크롤링
        chzzkchat.run()
    except KeyboardInterrupt:
        print('\n종료합니다.')
    finally:
        chzzkchat.close()