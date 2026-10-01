package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"time"

	"github.com/moov-io/cadeft"
)

type request struct {
	Action                    string    `json:"action"`
	RecordType                string    `json:"type"`
	TransactionTypeCode       string    `json:"txn_type"`
	Amount                    int64     `json:"amount"`
	DueDate                   time.Time `json:"due_date"`
	DateFundsAvailable        time.Time `json:"date_funds_available"`
	InstitutionID             string    `json:"institution_id"`
	PayorAccountNo            string    `json:"payor_account_no"`
	PayeeAccountNo            string    `json:"payee_account_no"`
	ItemTraceNo               string    `json:"item_trace_no"`
	OriginatorShortName       string    `json:"short_name"`
	PayorName                 string    `json:"payor_name"`
	PayeeName                 string    `json:"payee_name"`
	OriginatorLongName        string    `json:"long_name"`
	ReturnInstitutionID       string    `json:"return_institution_id"`
	ReturnAccountNo           string    `json:"return_account_no"`
	OriginalInstitutionID     string    `json:"original_institution_id"`
	OriginalAccountNo         string    `json:"original_account_no"`
	OriginalItemTraceNo       string    `json:"original_item_trace_no"`
	StoredTransactionType     string    `json:"stored_txn_type"`
	UserID                    string    `json:"user_id"`
	CrossRefNo                string    `json:"cross_ref_no"`
	SundryInfo                string    `json:"sundry_info"`
	SettlementCode            string    `json:"settlement_code"`
	InvalidDataElementID      string    `json:"invalid_data_element_id"`
	SerializedTransactionText string    `json:"serialized"`
}

func main() {
	input, err := io.ReadAll(os.Stdin)
	check(err)
	var req request
	check(json.Unmarshal(input, &req))

	txnDate := req.DueDate
	if req.DateFundsAvailable.Year() != 1 {
		txnDate = req.DateFundsAvailable
	}
	bankID, bankAccount := req.ReturnInstitutionID, req.ReturnAccountNo
	if bankID == "" {
		bankID, bankAccount = req.OriginalInstitutionID, req.OriginalAccountNo
	}
	opts := []cadeft.BaseTxnOpt{
		cadeft.WithStoredTransactionType(req.StoredTransactionType),
		cadeft.WithUserID(req.UserID),
		cadeft.WithCrossRefNo(req.CrossRefNo),
		cadeft.WithSundryInfo(req.SundryInfo),
		cadeft.WithSettlementCode(req.SettlementCode),
		cadeft.WithInvalidDataElementID(req.InvalidDataElementID),
	}
	txn := cadeft.NewTransaction(
		cadeft.RecordType(req.RecordType), cadeft.TransactionType(req.TransactionTypeCode),
		req.Amount, &txnDate, req.InstitutionID,
		firstNonEmpty(req.PayorAccountNo, req.PayeeAccountNo), req.ItemTraceNo,
		req.OriginatorShortName, firstNonEmpty(req.PayorName, req.PayeeName),
		req.OriginatorLongName, bankID, bankAccount, req.OriginalItemTraceNo, opts...,
	)
	if txn == nil {
		fatal(fmt.Errorf("unsupported record type %q", req.RecordType))
	}
	switch req.Action {
	case "build":
		var output string
		output, err = txn.Build()
		if err == nil {
			_, err = fmt.Fprint(os.Stdout, output)
		}
	case "parse":
		err = txn.Parse(req.SerializedTransactionText)
		if err == nil {
			var output []byte
			output, err = json.Marshal(txn)
			if err == nil {
				_, err = os.Stdout.Write(output)
			}
		}
	default:
		err = fmt.Errorf("unknown action %q", req.Action)
	}
	check(err)
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if value != "" {
			return value
		}
	}
	return ""
}

func check(err error) {
	if err != nil {
		fatal(err)
	}
}

func fatal(err error) {
	fmt.Fprintln(os.Stderr, err)
	os.Exit(1)
}
