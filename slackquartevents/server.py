from quart import Quart, request, make_response
import json
import platform
import sys
import hmac
import hashlib
from time import time
from .version import __version__


class SlackServer(Quart):
    def __init__(self, signing_secret, endpoint, emitter, server):
        self.signing_secret = signing_secret
        self.emitter = emitter
        self.endpoint = endpoint
        self.package_info = self.get_package_info()

        # If a server is passed in, bind the event handler routes to it,
        # otherwise create a new Quart instance.
        if server:
            if isinstance(server, Quart):
                self.bind_route(server)
            else:
                raise TypeError("Server must be an instance of Quart")
        else:
            Quart.__init__(self, __name__)
            self.bind_route(self)

    def get_package_info(self):
        client_name = __name__.split('.')[0]
        client_version = __version__  # Version is returned from version.py

        # Collect the package info, Python version and OS version.
        package_info = {
            "client": "{0}/{1}".format(client_name, client_version),
            "python": "Python/{v.major}.{v.minor}.{v.micro}".format(v=sys.version_info),
            "system": "{0}/{1}".format(platform.system(), platform.release())
        }

        # Concatenate and format the user-agent string to be passed into request headers
        ua_string = []
        for key, val in package_info.items():
            ua_string.append(val)

        return " ".join(ua_string)

    async def verify_signature(self, timestamp, signature):
        # Verify the request signature of the request sent from Slack
        # Generate a new hash using the app's signing secret and request data

        # Compare the generated hash and incoming request signature
        # Python 2.7.6 doesn't support compare_digest
        # It's recommended to use Python 2.7.7+
        # noqa See https://docs.python.org/2/whatsnew/2.7.html#pep-466-network-security-enhancements-for-python-2-7
        if hasattr(hmac, "compare_digest"):
            print('#################')
            print(type(timestamp))
            print(type(request.get_data()))
            print('##############################')
            code = await request.get_data()
            req = str.encode('v0:' + str(timestamp) + ':') + code
            request_hash = 'v0=' + hmac.new(
                str.encode(self.signing_secret),
                req, hashlib.sha256
            ).hexdigest()
            # Compare byte strings for Python 2
            if (sys.version_info[0] == 2):
                return hmac.compare_digest(bytes(request_hash), bytes(signature))
            else:
                return hmac.compare_digest(request_hash, signature)
        else:
            # So, we'll compare the signatures explicitly
            req = str.encode('v0:' + str(timestamp) + ':') + request.get_data()
            request_hash = 'v0=' + hmac.new(
                str.encode(self.signing_secret),
                req, hashlib.sha256
            ).hexdigest()

            if len(request_hash) != len(signature):
                return False
            result = 0
            if isinstance(request_hash, bytes) and isinstance(signature, bytes):
                for x, y in zip(request_hash, signature):
                    result |= x ^ y
            else:
                for x, y in zip(request_hash, signature):
                    result |= ord(x) ^ ord(y)
            return result == 0

    def bind_route(self, server):
        @server.route(self.endpoint, methods=['GET', 'POST'])
        async def event():
            # If a GET request is made, return 404.
            if request.method == 'GET':
                return make_response("This endpoint should only use POST requests", 404)

            # Each request comes with request timestamp and request signature
            # emit an error if the timestamp is out of range
            req_timestamp = request.headers.get('X-Slack-Request-Timestamp')
            if abs(time() - int(req_timestamp)) > 60 * 5:
                slack_exception = SlackEventAdapterException(
                    'Invalid request timestamp')
                self.emitter.emit('error', slack_exception)
                return make_response("", 403)

            # Verify the request signature using the app's signing secret
            # emit an error if the signature can't be verified
            req_signature = request.headers.get('X-Slack-Signature')
            if not self.verify_signature(req_timestamp, req_signature):
                slack_exception = SlackEventAdapterException(
                    'Invalid request signature')
                self.emitter.emit('error', slack_exception)
                return make_response("", 403)

            # Parse the request payload into JSON
            req_pay = await request.get_json()
            event_data = req_pay
            # event_data = json.loads(req_pay)

            # Echo the URL verification challenge code back to Slack
            if "challenge" in event_data:
                return make_response(
                    event_data.get("challenge"), 200, {
                        "content_type": "application/json"}
                )

            # Parse the Event payload and emit the event to the event listener
            if "event" in event_data:
                event_type = event_data["event"]["type"]
                self.emitter.emit(event_type, event_data)
                response = make_response("", 200)
                (await response).headers['X-Slack-Powered-By'] = 'something'
                return response


class SlackEventAdapterException(Exception):
    """
    Base exception for all errors raised by the SlackClient library
    """

    def __init__(self, msg=None):
        if msg is None:
            # default error message
            msg = "An error occurred in the SlackEventsApiAdapter library"
        super(SlackEventAdapterException, self).__init__(msg)
