const videoCopy = {
  ru: {
    uploadTitle: 'Видеофайлы', uploadDesc: 'Откройте видео, выделите области скрытия и сохраните результат.',
    pick: 'Открыть видео', replace: 'Другой файл', drop: 'Перетащите видео сюда', formats: 'MP4, WebM и другие форматы, поддерживаемые браузером. До 2 ГБ.',
    local: 'Локальный файл', invalidFile: 'Выберите видеофайл.', largeFile: 'Размер видео превышает 2 ГБ.', emptyFile: 'Этот файл пуст.',
    original: 'Оригинал', result: 'Области скрытия', addRegion: 'Добавить область', cancelDrawing: 'Отменить выделение', clear: 'Удалить области', undo: 'Удалить последнюю',
    drawHint: 'Выделите прямоугольник на изображении справа.', regionCount: 'Областей', playbackError: 'Не удалось открыть видео. Попробуйте MP4 или WebM.',
    automatic: 'Автоматическое распознавание не подключено', export: 'Сохранить видео', cancelExport: 'Отменить сохранение', preparing: 'Подготовка', recording: 'Сохранение', download: 'Скачать WebM',
    exportHint: 'Сохранение проходит за время воспроизведения видео.', audio: 'Сохранить исходный звук', audioError: 'Не удалось сохранить звук. Снимите флажок «Сохранить исходный звук» и повторите.',
    exportUnavailable: 'Сохранение видео недоступно в этом браузере.', exportError: 'Не удалось сохранить видео. Повторите попытку.', exportTooLarge: 'Запись превышает 512 МБ. Используйте более короткое видео.',
    exportBackground: 'Вернитесь на эту вкладку, чтобы продолжить сохранение.', resume: 'Продолжить сохранение', regionLabel: 'Область',
    cameraTitle: 'Камера', cameraDesc: 'Просмотр камеры и ручное скрытие выбранных областей.', cameraStart: 'Подключить', cameraStop: 'Отключить', cameraPending: 'Подключение…',
    cameraOff: 'Камера отключена', cameraOn: 'Камера подключена', cameraSelect: 'Устройство', cameraDefault: 'Камера по умолчанию', cameraName: 'Камера',
    cameraUnavailable: 'Доступ к камере недоступен. Откройте приложение через localhost или HTTPS.', cameraDenied: 'Нет разрешения на камеру. Разрешите доступ в браузере и повторите.',
    cameraMissing: 'Камера не найдена.', cameraError: 'Не удалось подключить камеру. Возможно, её использует другое приложение.', cameraRecord: 'Начать запись', cameraRecordStop: 'Завершить запись',
  },
  en: {
    uploadTitle: 'Video files', uploadDesc: 'Open a video, select regions to hide, and save the result.',
    pick: 'Open video', replace: 'Another file', drop: 'Drop a video here', formats: 'MP4, WebM and other browser-supported formats. Up to 2 GB.',
    local: 'Local file', invalidFile: 'Choose a video file.', largeFile: 'The video exceeds 2 GB.', emptyFile: 'This file is empty.',
    original: 'Original', result: 'Hidden regions', addRegion: 'Add region', cancelDrawing: 'Cancel selection', clear: 'Clear regions', undo: 'Remove last',
    drawHint: 'Draw a rectangle on the image on the right.', regionCount: 'Regions', playbackError: 'Could not open this video. Try MP4 or WebM.',
    automatic: 'Automatic recognition is not connected', export: 'Save video', cancelExport: 'Cancel saving', preparing: 'Preparing', recording: 'Saving', download: 'Download WebM',
    exportHint: 'Saving takes the duration of the video.', audio: 'Keep original audio', audioError: 'Could not record audio. Uncheck “Keep original audio” and try again.',
    exportUnavailable: 'Video saving is unavailable in this browser.', exportError: 'Could not save the video. Try again.', exportTooLarge: 'Recording exceeds 512 MB. Use a shorter video.',
    exportBackground: 'Return to this tab to continue saving.', resume: 'Continue saving', regionLabel: 'Region',
    cameraTitle: 'Camera', cameraDesc: 'View a camera and manually hide selected regions.', cameraStart: 'Connect', cameraStop: 'Disconnect', cameraPending: 'Connecting…',
    cameraOff: 'Camera disconnected', cameraOn: 'Camera connected', cameraSelect: 'Device', cameraDefault: 'Default camera', cameraName: 'Camera',
    cameraUnavailable: 'Camera access is unavailable. Open the app using localhost or HTTPS.', cameraDenied: 'Camera permission denied. Allow access in your browser and try again.',
    cameraMissing: 'No camera found.', cameraError: 'Could not connect to the camera. Another app may be using it.', cameraRecord: 'Start recording', cameraRecordStop: 'Finish recording',
  },
  kk: {
    uploadTitle: 'Бейнефайлдар', uploadDesc: 'Бейнені ашып, жасырылатын аймақтарды белгілеңіз және нәтижені сақтаңыз.',
    pick: 'Бейнені ашу', replace: 'Басқа файл', drop: 'Бейнені осында сүйреңіз', formats: 'MP4, WebM және браузер қолдайтын басқа форматтар. 2 ГБ дейін.',
    local: 'Жергілікті файл', invalidFile: 'Бейнефайл таңдаңыз.', largeFile: 'Бейне көлемі 2 ГБ-тан асады.', emptyFile: 'Бұл файл бос.',
    original: 'Түпнұсқа', result: 'Жасырылатын аймақтар', addRegion: 'Аймақ қосу', cancelDrawing: 'Белгілеуді тоқтату', clear: 'Аймақтарды жою', undo: 'Соңғысын жою',
    drawHint: 'Оң жақтағы суретте тіктөртбұрыш белгілеңіз.', regionCount: 'Аймақтар', playbackError: 'Бейнені ашу мүмкін болмады. MP4 немесе WebM қолданып көріңіз.',
    automatic: 'Автоматты тану қосылмаған', export: 'Бейнені сақтау', cancelExport: 'Сақтауды тоқтату', preparing: 'Дайындау', recording: 'Сақтау', download: 'WebM жүктеу',
    exportHint: 'Сақтау бейненің ұзақтығына тең уақыт алады.', audio: 'Бастапқы дыбысты сақтау', audioError: 'Дыбысты сақтау мүмкін болмады. «Бастапқы дыбысты сақтау» белгісін алып, қайталаңыз.',
    exportUnavailable: 'Бұл браузерде бейнені сақтау қолжетімсіз.', exportError: 'Бейнені сақтау мүмкін болмады. Қайталаңыз.', exportTooLarge: 'Жазба 512 МБ-тан асады. Қысқа бейнені пайдаланыңыз.',
    exportBackground: 'Сақтауды жалғастыру үшін осы қойындыға оралыңыз.', resume: 'Сақтауды жалғастыру', regionLabel: 'Аймақ',
    cameraTitle: 'Камера', cameraDesc: 'Камераны көру және таңдалған аймақтарды қолмен жасыру.', cameraStart: 'Қосу', cameraStop: 'Ажырату', cameraPending: 'Қосылуда…',
    cameraOff: 'Камера өшірулі', cameraOn: 'Камера қосылған', cameraSelect: 'Құрылғы', cameraDefault: 'Әдепкі камера', cameraName: 'Камера',
    cameraUnavailable: 'Камера қолжетімсіз. Қосымшаны localhost немесе HTTPS арқылы ашыңыз.', cameraDenied: 'Камераға рұқсат жоқ. Браузерде рұқсат беріп, қайталаңыз.',
    cameraMissing: 'Камера табылмады.', cameraError: 'Камераны қосу мүмкін болмады. Оны басқа қосымша пайдаланып жатқан болуы мүмкін.', cameraRecord: 'Жазуды бастау', cameraRecordStop: 'Жазуды аяқтау',
  },
} as const

export function useVideoCopy() {
  const { locale } = useLocale()
  return computed(() => videoCopy[locale.value])
}
