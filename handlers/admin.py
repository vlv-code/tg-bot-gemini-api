import html
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from config import settings
from keyboards import admin_panel_keyboard, admin_users_keyboard
from handlers.common import storage

logger = logging.getLogger(__name__)

router = Router()


# --- Панель администратора ---

@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    user_id = message.from_user.id
    if not await storage.is_user_admin(user_id):
        await message.answer("⛔️ У вас нет доступа к панели администратора.")
        return

    whitelist_enabled = await storage.is_whitelist_enabled()
    users = await storage.list_allowed_users()
    token_stats = await storage.get_token_stats()

    wl_status = "ВКЛЮЧЁН 🔒 (доступ только по списку)" if whitelist_enabled else "ВЫКЛЮЧЕН 🌍 (доступ для всех)"
    today_tok = f"{token_stats['today_total']:,}".replace(",", " ")
    all_tok = f"{token_stats['all_total']:,}".replace(",", " ")

    text = (
        "👑 <b>Панель управления администратора</b>\n\n"
        f"• <b>Белый список:</b> {wl_status}\n"
        f"• <b>Пользователей в базе:</b> {len(users)}\n"
        f"• <b>Расход токенов сегодня (все):</b> <code>{today_tok}</code>\n"
        f"• <b>Расход токенов всего:</b> <code>{all_tok}</code> ({token_stats['all_requests']} запросов)\n\n"
        "<b>Команды управления доступом:</b>\n"
        "• <code>/adduser &lt;id&gt; [username]</code> — добавить пользователя\n"
        "• <code>/addadmin &lt;id&gt; [username]</code> — назначить администратора\n"
        "• <code>/deluser &lt;id&gt;</code> — удалить из белого списка\n"
        "• <code>/users</code> — список всех пользователей"
    )
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=admin_panel_keyboard(whitelist_enabled),
    )


@router.callback_query(F.data == "menu:admin")
async def cb_menu_admin(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    if not await storage.is_user_admin(user_id):
        await callback.answer("⛔️ Нет прав администратора", show_alert=True)
        return

    whitelist_enabled = await storage.is_whitelist_enabled()
    users = await storage.list_allowed_users()
    token_stats = await storage.get_token_stats()

    wl_status = "ВКЛЮЧЁН 🔒 (доступ только по списку)" if whitelist_enabled else "ВЫКЛЮЧЕН 🌍 (доступ для всех)"
    today_tok = f"{token_stats['today_total']:,}".replace(",", " ")
    all_tok = f"{token_stats['all_total']:,}".replace(",", " ")

    text = (
        "👑 <b>Панель управления администратора</b>\n\n"
        f"• <b>Белый список:</b> {wl_status}\n"
        f"• <b>Пользователей в базе:</b> {len(users)}\n"
        f"• <b>Расход токенов сегодня (все):</b> <code>{today_tok}</code>\n"
        f"• <b>Расход токенов всего:</b> <code>{all_tok}</code> ({token_stats['all_requests']} запросов)\n\n"
        "<b>Команды управления доступом:</b>\n"
        "• <code>/adduser &lt;id&gt; [username]</code> — добавить пользователя\n"
        "• <code>/addadmin &lt;id&gt; [username]</code> — назначить администратора\n"
        "• <code>/deluser &lt;id&gt;</code> — удалить из белого списка\n"
        "• <code>/users</code> — список всех пользователей"
    )
    try:
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=admin_panel_keyboard(whitelist_enabled),
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data == "admin:toggle_whitelist")
async def cb_admin_toggle_whitelist(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    if not await storage.is_user_admin(user_id):
        await callback.answer("⛔️ Нет прав администратора", show_alert=True)
        return

    new_state = await storage.toggle_whitelist()
    status_str = "включен (доступ ограничен)" if new_state else "выключен (доступ открыт всем)"
    await callback.answer(f"Белый список {status_str} ✅", show_alert=True)

    users = await storage.list_allowed_users()
    token_stats = await storage.get_token_stats()
    wl_status = "ВКЛЮЧЁН 🔒 (доступ только по списку)" if new_state else "ВЫКЛЮЧЕН 🌍 (доступ для всех)"
    today_tok = f"{token_stats['today_total']:,}".replace(",", " ")
    all_tok = f"{token_stats['all_total']:,}".replace(",", " ")

    text = (
        "👑 <b>Панель управления администратора</b>\n\n"
        f"• <b>Белый список:</b> {wl_status}\n"
        f"• <b>Пользователей в базе:</b> {len(users)}\n"
        f"• <b>Расход токенов сегодня (все):</b> <code>{today_tok}</code>\n"
        f"• <b>Расход токенов всего:</b> <code>{all_tok}</code> ({token_stats['all_requests']} запросов)\n\n"
        "<b>Команды управления доступом:</b>\n"
        "• <code>/adduser &lt;id&gt; [username]</code> — добавить пользователя\n"
        "• <code>/addadmin &lt;id&gt; [username]</code> — назначить администратора\n"
        "• <code>/deluser &lt;id&gt;</code> — удалить из белого списка\n"
        "• <code>/users</code> — список всех пользователей"
    )
    try:
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=admin_panel_keyboard(new_state),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "admin:users")
async def cb_admin_users(callback: CallbackQuery) -> None:
    if not await storage.is_user_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет прав администратора", show_alert=True)
        return
    users = await storage.list_allowed_users()
    try:
        await callback.message.edit_text(
            f"👥 <b>Разрешённые пользователи ({len(users)}):</b>",
            parse_mode="HTML",
            reply_markup=admin_users_keyboard(users),
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("admin:del_user:"))
async def cb_admin_del_user(callback: CallbackQuery) -> None:
    caller_id = callback.from_user.id
    if not await storage.is_user_admin(caller_id):
        await callback.answer("⛔️ Нет прав администратора", show_alert=True)
        return

    target_uid_str = callback.data.split(":", 2)[2]
    try:
        target_uid = int(target_uid_str)
    except ValueError:
        await callback.answer("Ошибка ID пользователя", show_alert=True)
        return

    if target_uid in settings.admin_ids:
        await callback.answer("⛔️ Нельзя удалить суперадминистратора бота!", show_alert=True)
        return

    if await storage.is_user_admin(target_uid) and caller_id not in settings.admin_ids:
        await callback.answer("⛔️ Только главный суперадмин может удалять администраторов!", show_alert=True)
        return

    await storage.remove_allowed_user(target_uid)
    await callback.answer(f"Пользователь {target_uid} удален из белого списка ✅", show_alert=True)

    users = await storage.list_allowed_users()
    try:
        await callback.message.edit_text(
            f"👥 <b>Разрешённые пользователи ({len(users)}):</b>",
            parse_mode="HTML",
            reply_markup=admin_users_keyboard(users),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("admin:user_info:"))
async def cb_admin_user_info(callback: CallbackQuery) -> None:
    target_uid_str = callback.data.split(":", 2)[2]
    try:
        target_uid = int(target_uid_str)
        stats = await storage.get_token_stats(target_uid)
        u_today = f"{stats['today_total']:,}".replace(",", " ")
        u_all = f"{stats['all_total']:,}".replace(",", " ")
        await callback.answer(
            f"ID {target_uid}:\nСегодня: {u_today} токенов\nВсего: {u_all} ({stats['all_requests']} запросов)",
            show_alert=True,
        )
    except Exception:
        await callback.answer(f"Пользователь {target_uid_str}", show_alert=True)


@router.callback_query(F.data == "admin:add_user_hint")
async def cb_admin_add_user_hint(callback: CallbackQuery) -> None:
    await callback.answer(
        "Чтобы добавить пользователя, отправьте команду:\n/adduser <telegram_id> [никнейм]\n\nЧтобы сделать админом:\n/addadmin <telegram_id> [никнейм]",
        show_alert=True,
    )


# --- Текстовые команды администратора ---

@router.message(Command("adduser", "addadmin"))
async def cmd_adduser(message: Message) -> None:
    caller_id = message.from_user.id
    if not await storage.is_user_admin(caller_id):
        await message.answer("⛔️ У вас нет прав администратора.")
        return

    args = message.text.split() if message.text else []
    if len(args) < 2:
        await message.answer(
            "Использование:\n"
            "• <code>/adduser &lt;user_id&gt; [username]</code> — добавить пользователя\n"
            "• <code>/addadmin &lt;user_id&gt; [username]</code> — назначить администратора",
            parse_mode="HTML",
        )
        return

    try:
        target_uid = int(args[1])
    except ValueError:
        await message.answer("⚠️ ID пользователя должен быть числом.")
        return
    username = args[2] if len(args) > 2 else ""
    is_admin = args[0].lower().startswith("/addadmin")

    if is_admin and caller_id not in settings.admin_ids:
        await message.answer("⛔️ Только главный суперадминистратор может назначать других администраторов.")
        return

    await storage.add_allowed_user(
        user_id=target_uid,
        username=username,
        is_admin=is_admin,
        added_by=caller_id,
    )
    role_text = "Администратор" if is_admin else "Пользователь"
    await message.answer(
        f"✅ {role_text} <code>{target_uid}</code> ({username or 'без ника'}) добавлен в белый список!",
        parse_mode="HTML",
    )


@router.message(Command("deluser"))
async def cmd_deluser(message: Message) -> None:
    caller_id = message.from_user.id
    if not await storage.is_user_admin(caller_id):
        await message.answer("⛔️ У вас нет прав администратора.")
        return
    args = message.text.split() if message.text else []
    if len(args) < 2:
        await message.answer("Использование: <code>/deluser &lt;user_id&gt;</code>", parse_mode="HTML")
        return
    try:
        target_uid = int(args[1])
    except ValueError:
        await message.answer("⚠️ ID пользователя должен быть числом.")
        return
    if target_uid in settings.admin_ids:
        await message.answer("⛔️ Нельзя удалить суперадминистратора.")
        return
    if await storage.is_user_admin(target_uid) and caller_id not in settings.admin_ids:
        await message.answer("⛔️ Только суперадмин может удалять других администраторов.")
        return
    removed = await storage.remove_allowed_user(target_uid)
    if removed:
        await message.answer(f"✅ Пользователь <code>{target_uid}</code> удалён из белого списка.", parse_mode="HTML")
    else:
        await message.answer(f"ℹ️ Пользователь <code>{target_uid}</code> не был найден в базе.", parse_mode="HTML")


@router.message(Command("users"))
async def cmd_users(message: Message) -> None:
    if not await storage.is_user_admin(message.from_user.id):
        await message.answer("⛔️ У вас нет прав администратора.")
        return
    users = await storage.list_allowed_users()
    if not users:
        await message.answer("👥 В базе данных пока нет добавленных пользователей.")
        return
    await message.answer(
        f"👥 <b>Разрешённые пользователи ({len(users)}):</b>",
        parse_mode="HTML",
        reply_markup=admin_users_keyboard(users),
    )

