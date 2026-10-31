import logging
import logging.handlers

from tools import replay_log_redirect


def test_replay_redirects_rotating_and_file_handlers_without_changing_text(tmp_path):
    source = tmp_path / "shared_logs"
    output = tmp_path / "run" / "logs"
    source.mkdir()
    replay_log_redirect.install_replay_log_redirect(output)
    logger = logging.getLogger("test_replay_log_redirect")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    rotating = logging.handlers.RotatingFileHandler(source / "native.log", maxBytes=4096, backupCount=1, encoding="utf-8")
    rotating.setFormatter(logging.Formatter("%(levelname)s|%(message)s"))
    direct = logging.FileHandler(source / "direct.log", encoding="utf-8")
    direct.setFormatter(logging.Formatter("%(message)s"))
    already_routed = logging.FileHandler(output / "already.log", encoding="utf-8")
    already_routed.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(rotating)
    logger.addHandler(direct)
    logger.addHandler(already_routed)
    try:
        logger.info("same message body")
        for handler in logger.handlers:
            handler.flush()
        assert (output / "native.log").read_text() == "INFO|same message body\n"
        assert (output / "direct.log").read_text() == "same message body\n"
        assert (output / "already.log").read_text() == "same message body\n"
        assert not (output / "logs" / "already.log").exists()
        assert not (source / "native.log").exists()
        assert not (source / "direct.log").exists()
        assert logging.handlers.RotatingFileHandler is not replay_log_redirect._REDIRECT_STATE["original_rotating_handler"]
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers.clear()
        replay_log_redirect.uninstall_replay_log_redirect()
