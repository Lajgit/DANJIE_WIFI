#include "InterruptTask.h"
#include "CtrlTask.h"
#include "MesgTask.h"
#include "MainTask.h"
#include "CommTask.h"
#include "port_event.h"
#include "tim.h"
#include "stdio.h"

#define Mesg_Head 0xAA
#define Mesg_Tail 0x55
#define COIN_INPUT_DEBOUNCE_TIME 50U

/* TIM7每个计数为0.1ms，20个计数对应2ms。 */
#define HOOLLE_LOW_MIN_COUNT 20U
#define HOOLLE_INPUT_FILTER_MS 3U

volatile uint16_t HoolleInputPendingCount = 0U;

extern Event_Handle_t Mesg_event;
extern Event_Handle_t Event;
extern Motor_Hoolle Motor_Hoolle1, Motor_Hoolle2;
extern Motor_Card Card;
extern Switch_Valve Lock_Valve, Valve;
extern uint8_t LightBoard_Lightness;
extern uint8_t LightBelt_Lightness;
extern uint8_t sm16306s_data[2];
extern Rx_HandleTypeDef Rx1;
extern Rx_HandleTypeDef Rx3;

static uint32_t CoinInputLastTick = 0;
static uint8_t CoinInputTriggered = 0;

static uint8_t HoolleInputRawState = 1U;
static uint8_t HoolleInputStableState = 1U;
static uint8_t HoolleInputSameCount = 0U;

void HoolleInput_FilterInit(void)
{
    uint8_t CurrentState;

    CurrentState =
        HAL_GPIO_ReadPin(
            HoolleInput_GPIO_Port,
            HoolleInput_Pin) == GPIO_PIN_SET
            ? 1U
            : 0U;

    HoolleInputRawState = CurrentState;
    HoolleInputStableState = CurrentState;
    HoolleInputSameCount = 0U;
}

void HoolleInput_Scan1ms(void)
{
    uint8_t CurrentState;

    CurrentState =
        HAL_GPIO_ReadPin(
            HoolleInput_GPIO_Port,
            HoolleInput_Pin) == GPIO_PIN_SET
            ? 1U
            : 0U;

    if (CurrentState == HoolleInputRawState)
    {
        if (HoolleInputSameCount < HOOLLE_INPUT_FILTER_MS)
        {
            HoolleInputSameCount++;
        }
    }
    else
    {
        HoolleInputRawState = CurrentState;
        HoolleInputSameCount = 1U;
    }

    if (HoolleInputSameCount >= HOOLLE_INPUT_FILTER_MS &&
        HoolleInputStableState != HoolleInputRawState)
    {
        HoolleInputStableState = HoolleInputRawState;

        if (HoolleInputStableState == 0U &&
            HoolleInputPendingCount < 0xFFFFU)
        {
            HoolleInputPendingCount++;
        }
    }
}

static void HoolleInput_IRQ(void)
{
}

static void CoinInput_IRQ(void)
{
    uint32_t CurrentTick = HAL_GetTick();

    // 投币器有效低电平脉冲约37.8ms，50ms内的重复上升沿视为抖动
    if (CoinInputTriggered == 0 || CurrentTick - CoinInputLastTick >= COIN_INPUT_DEBOUNCE_TIME)
    {
        CoinInputLastTick = CurrentTick;
        CoinInputTriggered = 1;
        EventGroupSetBits(&Mesg_event, MesgEvent_CoinInput);
    }
}

static void Hoolle_1_Output_IRQ(void)
{
    static uint8_t LastBallStopped = 0U;
    uint32_t LowCount;

    if (HAL_GPIO_ReadPin(
            HoolleOutput_1_GPIO_Port,
            HoolleOutput_1_Pin) == GPIO_PIN_RESET)
    {
        __HAL_TIM_SetCounter(&htim7, 0);
        Motor_Hoolle1.Motor.ResetRuntime(
            &Motor_Hoolle1.Motor);

        if (Motor_Hoolle1.Hoolle_num == 1U &&
            Motor_Hoolle1.Motor.state == DEVICE_STATE_BUSY)
        {
            Motor_Hoolle1.Motor.Stop(
                &Motor_Hoolle1.Motor);
            LastBallStopped = 1U;
        }
        else
        {
            LastBallStopped = 0U;
        }

        return;
    }

    LowCount = __HAL_TIM_GetCounter(&htim7);

    if (LowCount > HOOLLE_LOW_MIN_COUNT)
    {
        EventGroupSetBits(
            &Mesg_event,
            MesgEvent_RemainingHoolle);

        if (Motor_Hoolle1.Hoolle_num > 0U)
        {
            Motor_Hoolle1.Hoolle_num--;
            Motor_Hoolle1.RetryCount = 0;

            if (Motor_Hoolle1.Hoolle_num == 0U &&
                Motor_Hoolle1.Motor.state != DEVICE_STATE_IDLE)
            {
                Motor_Hoolle1.Motor.state =
                    DEVICE_STATE_STOP;
            }
        }

        LastBallStopped = 0U;
        return;
    }

    if (LastBallStopped != 0U &&
        Motor_Hoolle1.Hoolle_num == 1U &&
        Motor_Hoolle1.Motor.state == DEVICE_STATE_BUSY)
    {
        Motor_Hoolle1.Motor.state =
            DEVICE_STATE_START;
    }

    LastBallStopped = 0U;
}

static void Hoolle_2_Output_IRQ(void)
{
    static uint8_t LastBallStopped = 0U;
    uint32_t LowCount;

    if (HAL_GPIO_ReadPin(
            HoolleOutput_2_GPIO_Port,
            HoolleOutput_2_Pin) == GPIO_PIN_RESET)
    {
        __HAL_TIM_SetCounter(&htim7, 0);
        Motor_Hoolle2.Motor.ResetRuntime(
            &Motor_Hoolle2.Motor);

        if (Motor_Hoolle2.Hoolle_num == 1U &&
            Motor_Hoolle2.Motor.state == DEVICE_STATE_BUSY)
        {
            Motor_Hoolle2.Motor.Stop(
                &Motor_Hoolle2.Motor);
            LastBallStopped = 1U;
        }
        else
        {
            LastBallStopped = 0U;
        }

        return;
    }

    LowCount = __HAL_TIM_GetCounter(&htim7);

    if (LowCount > 1U)
    {
        if (Motor_Hoolle2.Hoolle_num > 0U)
        {
            Motor_Hoolle2.Hoolle_num--;
            Motor_Hoolle2.RetryCount = 0;

            if (Motor_Hoolle2.Hoolle_num == 0U &&
                Motor_Hoolle2.Motor.state != DEVICE_STATE_IDLE)
            {
                Motor_Hoolle2.Motor.state =
                    DEVICE_STATE_STOP;
            }
        }

        LastBallStopped = 0U;
        return;
    }

    if (LastBallStopped != 0U &&
        Motor_Hoolle2.Hoolle_num == 1U &&
        Motor_Hoolle2.Motor.state == DEVICE_STATE_BUSY)
    {
        Motor_Hoolle2.Motor.state =
            DEVICE_STATE_START;
    }

    LastBallStopped = 0U;
}

static void CardOutput_IRQ(void)
{
    Card.Switch.ResetRuntime(&Card.Switch);
    if (Card.Card_num > 0)
    {
        Card.Card_num--;
        EventGroupSetBits(&Mesg_event, MesgEvent_CardOutputOnce); // 吐卡一次
        if (Card.Card_num <= 0 && Card.Switch.state != DEVICE_STATE_IDLE)
        {
            Card.Switch.state = DEVICE_STATE_STOP;
            EventGroupSetBits(&Mesg_event, MesgEvent_CardOutputFinish); // 吐卡完成
        }
    }
}

void HAL_GPIO_EXTI_Callback(uint16_t GPIO_Pin)
{
    switch (GPIO_Pin)
    {
    case HoolleInput_Pin:
        HoolleInput_IRQ();
        break;
    case CoinInput_Pin:
        CoinInput_IRQ();
        break;
    case HoolleOutput_1_Pin:
        Hoolle_1_Output_IRQ();
        break;
    case HoolleOutput_2_Pin:
        Hoolle_2_Output_IRQ();
        break;
    case CardFeedback_Pin:
        CardOutput_IRQ();
        break;
    }
}

void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart == Rx1.Handle.huart)
    {
        Rx1.Handle.RingBuf.f_WriteByte(&Rx1.Handle.RingBuf, Rx1.Handle.temp_data);
        HAL_UART_Receive_IT(huart, &Rx1.Handle.temp_data, 1);
    }
    if (huart == Rx3.Handle.huart)
    {
        Rx3.Handle.RingBuf.f_WriteByte(&Rx3.Handle.RingBuf, Rx3.Handle.temp_data);
        HAL_UART_Receive_IT(huart, &Rx3.Handle.temp_data, 1);
    }
}