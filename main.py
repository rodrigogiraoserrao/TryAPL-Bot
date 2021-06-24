import itertools, logging, os, sys, time, urllib.parse
import PIL.Image, PIL.ImageDraw, PIL.ImageFont
import requests, tweepy

try:
    import env
except ImportError:
    pass
CONSUMER_KEY = os.environ.get("BOT_CONSUMER_KEY")
CONSUMER_SECRET = os.environ.get("BOT_CONSUMER_SECRET")
ACCESS_TOKEN = os.environ.get("BOT_ACCESS_TOKEN")
ACCESS_TOKEN_SECRET = os.environ.get("BOT_ACCESS_TOKEN_SECRET")

TRYAPL_ENDPOINT = "https://tryapl.org/Exec"
SPECIAL_RESULT_TAG = chr(8) # Character used to tag special results from the TryAPL endpoint.

WAIT_BETWEEN_REQUESTS = 12

logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
formatter = logging.Formatter("%(asctime)s - %(name)-12s - %(levelname)-8s - %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)

if None in [CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET]:
    logger.error("Could not load API keys -- exiting.")
    sys.exit()

auth = tweepy.OAuthHandler(CONSUMER_KEY, CONSUMER_SECRET)
auth.set_access_token(ACCESS_TOKEN, ACCESS_TOKEN_SECRET)
api = tweepy.API(auth)

def load_most_recent_processed(backoff=0.1):
    """Fetch the id of the most recently processed tweet upon bot start."""

    try:
        return api.user_timeline(count=1)[0].id
    except tweepy.error.TweepError:
        logger.exception(
            "Failed to load most recent processed."
            f"Backing off for {backoff}s and retrying."
        )
        time.sleep(backoff)
        return load_most_recent_processed(backoff*2)

def save_most_recent_processed(mrp):
    """Ensure the id of the most recently processed tweet persists."""
    with open("most_recent_processed", "w") as f:
        f.write(str(mrp))

def parse_tweet(s):
    """Parse tweet for the code expressions to run."""

    in_code = in_string = False
    matches = []
    i = 0
    while i < len(s):
        char = s[i]
        if not in_code and char == "`":
            in_code = True
            match = ""
        elif in_code and char == "\n":
            in_string = False
            matches.append(match)
            match = ""
        elif in_code and not in_string:
            if char == "`":
                in_code = False
                matches.append(match)
            else:
                match += char
                in_string = char == "'"
        elif in_code and in_string:
            if char == "'" and i < len(s) - 1 and s[i+1] == "'":
                match += "''"
                i += 1
            else:
                in_string = char != "'"
                match += char
        i += 1
    return matches

def skip_tweet(tweet):
    """Check if this tweet should not be evaluated, to prevent recursion.

    This prevents users from evaluating expressions that evaluate to
    single-line results that would call the bot again, as that might
    create infinite recursion.
    Only exceptions are the bot's own original tweets.
    """

    return (
        tweet.user.screen_name == self_name and
        (rep_to := tweet.in_reply_to_screen_name) and
        rep_to != self_name
    )

# cf. https://github.com/twitter/twitter-text/tree/master/config
# to check what is the weight of each character in a Tweet.
SINGLE_CHAR_RANGES = [
    range(   0, 4351 + 1),
    range(8192, 8205 + 1),
    range(8208, 8223 + 1),
    range(8242, 8247 + 1),
]
def char_weight(char):
    """Returns weight value of a single character.

    Uses information from `twitter-text` to know the weight of each char.
    """
    if any(ord(char) in r for r in SINGLE_CHAR_RANGES):
        return 1
    else:
        return 2

def trim_to_twitter_length(string, max_length):
    """Trim a string so that it has, at most, the given Twitter length.

    This is not the same as doing string[:max_length] because Twitter assigns
    different weights to different characters.
    """

    acc = itertools.accumulate(char_weight(char) for char in string)
    trimmed = ""
    for char, weight in zip(string, acc):
        if weight > max_length:
            break
        trimmed += char
    return trimmed

def build_reply_text(code_matches, result_lines):
    """Builds the textual part of the reply to a tweet.
    
    Includes a direct link to TryAPL with the user code.
    If all results boil down to a single line,
    trim it to fit the screen and include it.
    Otherwise, do not attempt to send multiline output in the text.
    """

    if not code_matches:
        return (
            "I see you mentioned me, but I found no code to evaluate.\n"
            "\n"
            "Did you forget to put backticks around your code?"
        )

    REPLY_TEMPLATE = "{result}\n\nRun it online: {link}"
    # Produce the link to TryAPL.
    code = " ⋄ ".join(code_matches)
    base_reply = "{}\n\nRun it online: {}"
    tryapl_link = f"https://tryapl.org/?q={urllib.parse.quote_plus(code)}&run"

    results = list(itertools.chain(*result_lines))  # Flatten the list.
    # Do nothing with multiline output.
    if len(results) != 1:
        return base_reply.format("", tryapl_link).strip()

    # According to https://help.twitter.com/en/using-twitter/how-to-tweet-a-link,
    # URLs take up 23 characters.
    # We also subtract the length of the reply template.
    result = trim_to_twitter_length(results[0], 280 - 23 - len(base_reply))
    # If we had to trim something, make sure it is clear in the final result.
    if len(result) < len(results[0]):
        result = result[:-len("...")] + "..."
    return base_reply.format(result, tryapl_link)

def build_transcript(inputs, result_lines):
    """Build a session transcript from a series of inputs and results."""
    return "\n".join(
        " "*6 + inp + ("\n" if res else "") + "\n".join(res)
        for inp, res in zip(inputs, result_lines)
    )

def generate_image(result_lines):
    """Generate an image with the code results.

    cf. https://stackoverflow.com/q/5414639/2828287.
    """

    if len(result_lines) > 100:
        result_lines = result_lines[:98] + ["∙∙∙"] + result_lines[99]
    result_lines = [
        line if len(line) <= 100 else line[:99] + "∙"
        for line in result_lines
    ]
    fontsize = 18
    px_per_char, px_per_line = 11, 22       # Figured these out through experimenting by hand.
    longest_line = max(len(line) for line in result_lines)
    img_width = max(longest_line*px_per_char, 400)
    img_height = max(len(result_lines)*px_per_line, 300)
    image = PIL.Image.new(
        "RGBA",
        (img_width, img_height),
        (255, 255, 255),
    )
    draw = PIL.ImageDraw.Draw(image)
    font = PIL.ImageFont.truetype("resources/Apl385.ttf", fontsize)
    draw.text((0, 0), "\n".join(result_lines), (0, 0, 0), font=font)
    return image

most_recent_processed = load_most_recent_processed()
self_name = api.me().screen_name
called = time.time() - WAIT_BETWEEN_REQUESTS

while True:
    # I can request the mentions 75×/15 min, which gives around once every 12 seconds.
    # Sleep 12 seconds between requests, but discount any time I may have spent
    # processing previous tweets.
    time.sleep(
        max(0, WAIT_BETWEEN_REQUESTS - (time.time() - called) + 0.01)
    )
    try:
        to_process = api.mentions_timeline(most_recent_processed, tweet_mode="extended")[::-1]
    except tweepy.error.RateLimitError:
        logger.warning("Rate limit reached; waiting.")
        continue
    except tweepy.error.TweepError:
        logger.exception("Failed to load bot mentions.")
        continue
    called = time.time()

    if to_process:
        logger.info(f"Processing {len(to_process)} tweet(s).")
    else:
        logger.debug("Processing 0 tweet(s).")

    for tweet in to_process:
        if skip_tweet(tweet):
            logger.info(f"Skipping potential infinite recursion from tweet {tweet.id}.")
            most_recent_processed = tweet.id
            save_most_recent_processed(most_recent_processed)
            continue

        # Look for the code expressions to be ran.
        code_matches = parse_tweet(tweet.full_text)

        # Build the mock interpreter session from the parsed code.
        result_lines = []
        tags = []
        ws_state, ws_id, ws_hash = "", 0, ""
        for match in code_matches:
            ws_state, ws_id, ws_hash, res = requests.post(
                TRYAPL_ENDPOINT,
                json=[ws_state, ws_id, ws_hash, match],
            ).json()
            # Look for tagged result lines.
            for idx, line in enumerate(res):
                if line.startswith(SPECIAL_RESULT_TAG):
                    _, tag, result = line.split(SPECIAL_RESULT_TAG)
                    tags.append(tag)
                    res[idx] = result
            result_lines.append(res)

        # Handle the special case of a single ]help command differently,
        # by only replying with the URL response of the help command.
        if (
            len(tags) == 1 and tags[0] == "help" and
            len(result_lines) == 1 and len(result_lines[0]) == 1
        ):
            reply = result_lines[0][0]
        # Otherwise, just build the text reply.
        else:
            reply = build_reply_text(code_matches, result_lines)

        # Build the image attachment.
        session_transcript = build_transcript(code_matches, result_lines)
        if session_transcript:
            image = generate_image(session_transcript)
            image.save("img.png")
            media_ids = [api.media_upload("img.png").media_id_string]
        else:
            media_ids = []

        # Upload the reply.
        api.update_status(
            reply,
            in_reply_to_status_id=tweet.id,
            auto_populate_reply_metadata=True,
            media_ids=media_ids,
        )
        logger.info(f"Processed tweet {tweet.id} with {len(code_matches)} eval(s).")
        save_most_recent_processed(tweet.id)
        most_recent_processed = tweet.id
