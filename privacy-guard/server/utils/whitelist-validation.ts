import type { PlateCountry } from '../../shared/types/whitelist'

export class WhitelistError extends Error {
  constructor(public readonly statusCode: number, message: string) {
    super(message)
    this.name = 'WhitelistError'
  }
}

export const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

export function validateId(value: unknown): string {
  if (typeof value !== 'string' || !UUID_PATTERN.test(value)) {
    throw new WhitelistError(404, 'Запись не найдена.')
  }
  return value.toLowerCase()
}

export function objectBody(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new WhitelistError(400, 'Проверьте данные формы.')
  }
  return value as Record<string, unknown>
}

export function onlyFields(body: Record<string, unknown>, fields: readonly string[]): void {
  if (Object.keys(body).some(key => !fields.includes(key))) {
    throw new WhitelistError(400, 'В форме есть неподдерживаемые поля.')
  }
}

export function labelText(value: unknown): string {
  if (typeof value !== 'string') throw new WhitelistError(400, 'Укажите имя или название.')
  const text = value.trim()
  if (!text || text.length > 80 || /[\u0000-\u001f\u007f]/u.test(text)) {
    throw new WhitelistError(400, 'Название должно содержать от 1 до 80 символов.')
  }
  return text
}

export function notesText(value: unknown): string {
  if (value === undefined) return ''
  if (typeof value !== 'string') throw new WhitelistError(400, 'Проверьте примечание.')
  const text = value.trim()
  if (text.length > 500 || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/u.test(text)) {
    throw new WhitelistError(400, 'Примечание должно быть не длиннее 500 символов.')
  }
  return text
}

export function enabledFlag(value: unknown): boolean {
  if (typeof value !== 'boolean') throw new WhitelistError(400, 'Проверьте состояние записи.')
  return value
}

export function plateCountry(value: unknown): PlateCountry {
  if (value === undefined || value === '') return 'KZ'
  if (typeof value !== 'string') throw new WhitelistError(400, 'Выберите страну номера.')
  const country = value.trim().toUpperCase()
  if (country !== 'KZ' && country !== 'RU' && country !== 'OTHER') {
    throw new WhitelistError(400, 'Выберите Казахстан, Россию или другую страну.')
  }
  return country
}

const CYRILLIC_TO_LATIN: Record<string, string> = {
  А: 'A', В: 'B', Е: 'E', К: 'K', М: 'M', Н: 'H', О: 'O', Р: 'P',
  С: 'C', Т: 'T', У: 'Y', Х: 'X', І: 'I',
}

export function normalizePlate(value: unknown): string {
  if (typeof value !== 'string' || value.length > 40) {
    throw new WhitelistError(400, 'Укажите автомобильный номер.')
  }
  const plate = value.trim().toUpperCase().replace(/[\s-]+/gu, '')
    .replace(/[АВЕКМНОРСТУХІ]/gu, char => CYRILLIC_TO_LATIN[char]!)
  if (!/^[A-Z0-9]{4,12}$/u.test(plate) || !/[0-9]/u.test(plate)) {
    throw new WhitelistError(400, 'Номер: от 4 до 12 букв и цифр, хотя бы одна цифра.')
  }
  return plate
}
