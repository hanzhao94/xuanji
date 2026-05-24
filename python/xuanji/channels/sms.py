"""
xuanji 短信渠道

支持阿里云短信API和腾讯云短信API。
零外部依赖，使用urllib.request标准库。

用法:
    from xuanji.channels.sms import SMSChannel
    
    channel = SMSChannel()
    await channel.connect({
        "provider": "aliyun",  # or "tencent"
        "access_key_id": "...",
        "access_key_secret": "...",
        "sign_name": "...",
        "template_code": "...",
    })
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen
from urllib.parse import urlencode, quote

from xuanji.channels._base import ChannelBase, ChatType, ContentType, Message

logger = logging.getLogger("xuanji.channels.sms")


class SMSChannel(ChannelBase):
    """短信通信渠道

    支持阿里云和腾讯云短信服务。
    """

    name = "sms"
    description = "短信渠道（阿里云/腾讯云短信API）"

    ALIYUN_ENDPOINT = "dysmsapi.aliyuncs.com"
    TENCENT_ENDPOINT = "sms.tencentcloudapi.com"

    def __init__(self):
        super().__init__()
        self._config: Dict[str, Any] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._recent_messages: list = []

    async def connect(self, config: Dict) -> None:
        self._config = config
        provider = config.get("provider", "aliyun")
        if provider not in ("aliyun", "tencent"):
            raise ValueError(f"不支持的短信提供商: {provider}")

        self._connected = True
        logger.info(f"短信渠道已连接 (provider={provider})")

    async def listen(self) -> None:
        if not self._connected:
            raise RuntimeError("未连接")
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        # 短信是单向的，监听无意义
        while self._connected:
            await asyncio.sleep(60)

    async def send_text(self, target: str, text: str) -> None:
        """发送短信
        
        Args:
            target: 手机号（支持逗号分隔多个）
            text: 短信内容
        """
        provider = self._config.get("provider", "aliyun")
        if provider == "aliyun":
            await self._send_aliyun(target, text)
        else:
            await self._send_tencent(target, text)

    async def _send_aliyun(self, phone: str, text: str) -> None:
        """阿里云短信"""
        access_key_id = self._config.get("access_key_id", "")
        access_key_secret = self._config.get("access_key_secret", "")
        sign_name = self._config.get("sign_name", "")
        template_code = self._config.get("template_code", "")
        region = self._config.get("region", "cn-hangzhou")

        params = {
            "AccessKeyId": access_key_id,
            "Action": "SendSms",
            "Version": "2017-05-25",
            "Format": "JSON",
            "SignatureMethod": "HMAC-SHA1",
            "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "SignatureVersion": "1.0",
            "SignatureNonce": uuid.uuid4().hex,
            "RegionId": region,
            "PhoneNumbers": phone,
            "SignName": sign_name,
            "TemplateCode": template_code,
            "TemplateParam": json.dumps({"text": text}),
        }

        # 构建签名
        sorted_params = sorted(params.items())
        canonical = "&".join(f"{quote(k)}={quote(v)}" for k, v in sorted_params)
        string_to_sign = f"GET&{quote('/')}&{quote(canonical)}"
        sign_key = (access_key_secret + "&").encode("utf-8")
        signature = hmac.new(sign_key, string_to_sign.encode("utf-8"), hashlib.sha1).digest()
        import base64
        signature = base64.b64encode(signature).decode("utf-8")

        url = f"https://{self.ALIYUN_ENDPOINT}/?{canonical}&Signature={quote(signature)}"
        try:
            req = Request(url)
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("Code") != "OK":
                    logger.error(f"阿里云短信发送失败: {data}")
        except Exception as e:
            logger.error(f"阿里云短信异常: {e}")

    async def _send_tencent(self, phone: str, text: str) -> None:
        """腾讯云短信"""
        secret_id = self._config.get("access_key_id", "")
        secret_key = self._config.get("access_key_secret", "")
        sdk_app_id = self._config.get("sdk_app_id", "")
        sign_name = self._config.get("sign_name", "")
        template_id = self._config.get("template_code", "")
        region = self._config.get("region", "ap-guangzhou")

        timestamp = int(time.time())
        date = time.strftime("%Y-%m-%d", time.gmtime(timestamp))

        payload = {
            "PhoneNumberSet": [f"+86{phone}"] if not phone.startswith("+") else [phone],
            "SmsSdkAppId": sdk_app_id,
            "SignName": sign_name,
            "TemplateId": template_id,
            "TemplateParamSet": [text],
        }

        # 腾讯云HMAC-SHA256签名
        algorithm = "TC3-HMAC-SHA256"
        service = "sms"
        host = self.TENCENT_ENDPOINT
        action = "SendSms"

        canonical_request = f"POST\n/\n\ncontent-type:application/json; charset=utf-8\nhost:{host}\n\ncontent-type;host\n{hashlib.sha256(json.dumps(payload).encode()).hexdigest()}"
        credential_scope = f"{date}/{service}/tc3_request"
        string_to_sign = f"{algorithm}\n{timestamp}\n{credential_scope}\n{hashlib.sha256(canonical_request.encode()).hexdigest()}"

        def sign(key, msg):
            return hmac.new(key, msg.encode(), hashlib.sha256).digest()

        secret_date = sign(("TC3" + secret_key).encode(), date)
        secret_service = sign(secret_date, service)
        secret_signing = sign(secret_service, "tc3_request")
        signature = hmac.new(secret_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()

        authorization = f"{algorithm} Credential={secret_id}/{credential_scope}, SignedHeaders=content-type;host, Signature={signature}"

        url = f"https://{host}/"
        req = Request(url, data=json.dumps(payload).encode("utf-8"),
                      headers={
                          "Content-Type": "application/json; charset=utf-8",
                          "Host": host,
                          "X-TC-Action": action,
                          "X-TC-Timestamp": str(timestamp),
                          "X-TC-Version": "2021-01-11",
                          "X-TC-Region": region,
                          "Authorization": authorization,
                      }, method="POST")
        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                resp_data = data.get("Response", {})
                if resp_data.get("Error"):
                    logger.error(f"腾讯云短信发送失败: {resp_data}")
        except Exception as e:
            logger.error(f"腾讯云短信异常: {e}")

    async def send_image(self, target: str, image: Any) -> None:
        logger.info(f"[短信] 不支持发送图片到 {target}")

    async def send_file(self, target: str, path: str) -> None:
        logger.info(f"[短信] 不支持发送文件到 {target}")

    async def send_voice(self, target: str, audio: Any) -> None:
        logger.info(f"[短信] 不支持发送语音到 {target}")

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("短信渠道已断开")
